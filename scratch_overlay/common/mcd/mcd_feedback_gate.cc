// common/mcd/mcd_feedback_gate.cc
//
// Implementation of the MCD feedback gate — see mcd_feedback_gate.h.
//
// Design note on callback re-binding:
//   NrSlHelper::PrepareUeForSidelink() wires:
//     spectrumPhy->SetPhySlHarqFeedbackCallback(
//         MakeCallback(&NrUePhy::EnqueueSlHarqFeedback, nrUePhy));
//   We re-call SetPhySlHarqFeedbackCallback() with a shim that:
//     - is an ns3::Object subclass so it participates in ns3's Ptr<> ref-counting
//     - captures (rxMobility, mcdMeters, originalUePhy)
//     - resolves TX mobility from the global RntiMobilityRegistry
//     - drops the feedback when distance > mcdMeters, forwards otherwise.
//   MakeCallback(&McdGateShim::OnFeedback, shimPtr) gives us a valid
//   Callback<void, const SlHarqInfo&> without any std::function.

#include "mcd_feedback_gate.h"

#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/node.h"
#include "ns3/nr-spectrum-phy.h"
#include "ns3/nr-ue-net-device.h"
#include "ns3/nr-ue-phy.h"
#include "ns3/object.h"
#include "ns3/simulator.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("McdFeedbackGate");

// ---------------------------------------------------------------------------
// RntiMobilityRegistry
// ---------------------------------------------------------------------------

/* static */
RntiMobilityRegistry&
RntiMobilityRegistry::Instance()
{
    static RntiMobilityRegistry s_instance;
    return s_instance;
}

void
RntiMobilityRegistry::Register(uint16_t rnti, uint8_t bwpId, Ptr<MobilityModel> mob)
{
    m_map[{rnti, bwpId}] = mob;
    NS_LOG_INFO("McdFeedbackGate: registered rnti=" << rnti
                << " bwp=" << +bwpId);
}

Ptr<MobilityModel>
RntiMobilityRegistry::Lookup(uint16_t rnti, uint8_t bwpId) const
{
    auto it = m_map.find({rnti, bwpId});
    if (it == m_map.end())
    {
        NS_LOG_WARN("McdFeedbackGate: rnti=" << rnti << " bwp=" << +bwpId
                    << " not in registry — feedback NOT suppressed");
        return nullptr;
    }
    return it->second;
}

// ---------------------------------------------------------------------------
// McdGateShim: ns3::Object so Ptr<> ref-counting is safe.
// Wraps one UE's spectrum-phy feedback slot.
// ---------------------------------------------------------------------------

class McdGateShim : public Object
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::McdGateShim")
                                .SetParent<Object>()
                                .SetGroupName("NrV2xCommon");
        return tid;
    }

    void Setup(Ptr<MobilityModel> rxMob,
               double mcdMeters,
               Ptr<NrUePhy> uePhy)
    {
        m_rxMob    = rxMob;
        m_mcdMeters = mcdMeters;
        m_uePhy    = uePhy;
    }

    // This is the re-bound callback — invoked by NrSpectrumPhy in place of
    // NrUePhy::EnqueueSlHarqFeedback.
    void OnFeedback(const SlHarqInfo& info)
    {
        Ptr<MobilityModel> txMob =
            RntiMobilityRegistry::Instance().Lookup(info.m_txRnti, info.m_bwpIndex);

        if (txMob)
        {
            const double dist = m_rxMob->GetDistanceFrom(txMob);
            if (dist > m_mcdMeters)
            {
                NS_LOG_DEBUG("McdFeedbackGate: suppressing feedback"
                             << " txRnti=" << info.m_txRnti
                             << " dist=" << dist
                             << "m > mcd=" << m_mcdMeters << "m");
                return; // suppress
            }
            NS_LOG_DEBUG("McdFeedbackGate: passing feedback"
                         << " txRnti=" << info.m_txRnti
                         << " dist=" << dist
                         << "m <= mcd=" << m_mcdMeters << "m");
        }
        // TX not in registry or within MCD — forward.
        m_uePhy->EnqueueSlHarqFeedback(info);
    }

  private:
    Ptr<MobilityModel> m_rxMob;
    double             m_mcdMeters{1e9};
    Ptr<NrUePhy>       m_uePhy;
};

NS_OBJECT_ENSURE_REGISTERED(McdGateShim);

// ---------------------------------------------------------------------------
// RegisterRntiMobility
// ---------------------------------------------------------------------------

void
RegisterRntiMobility(const NetDeviceContainer& ueNetDev)
{
    RntiMobilityRegistry& reg = RntiMobilityRegistry::Instance();

    for (uint32_t i = 0; i < ueNetDev.GetN(); ++i)
    {
        auto dev = DynamicCast<NrUeNetDevice>(ueNetDev.Get(i));
        if (!dev)
            continue;

        Ptr<MobilityModel> mob = dev->GetNode()->GetObject<MobilityModel>();
        if (!mob)
        {
            NS_LOG_WARN("RegisterRntiMobility: node " << dev->GetNode()->GetId()
                        << " has no MobilityModel");
            continue;
        }

        const uint8_t numBwps = dev->GetCcMapSize();
        for (uint8_t bwpId = 0; bwpId < numBwps; ++bwpId)
        {
            Ptr<NrUePhy> uePhy = dev->GetPhy(bwpId);
            if (!uePhy)
                continue;
            uint16_t rnti = uePhy->GetRnti();
            if (rnti == 0)
            {
                NS_LOG_WARN("RegisterRntiMobility: node "
                            << dev->GetNode()->GetId()
                            << " bwp=" << +bwpId
                            << " RNTI=0 (not yet assigned?) — skipping");
                continue;
            }
            reg.Register(rnti, bwpId, mob);
        }
    }
}

void
RegisterRntiMobilityPoll(const NetDeviceContainer& ueNetDev)
{
    // Check if all UEs have valid (non-zero) RNTIs.
    bool allReady = true;
    for (uint32_t i = 0; i < ueNetDev.GetN(); ++i)
    {
        auto dev = DynamicCast<NrUeNetDevice>(ueNetDev.Get(i));
        if (!dev)
            continue;
        const uint8_t numBwps = dev->GetCcMapSize();
        for (uint8_t bwpId = 0; bwpId < numBwps; ++bwpId)
        {
            Ptr<NrUePhy> uePhy = dev->GetPhy(bwpId);
            if (!uePhy)
                continue;
            if (uePhy->GetRnti() == 0)
            {
                allReady = false;
                break;
            }
        }
        if (!allReady)
            break;
    }

    if (allReady)
    {
        NS_LOG_INFO("RegisterRntiMobilityPoll: all UEs have RNTIs — registering mobility");
        RegisterRntiMobility(ueNetDev);
    }
    else
    {
        // Reschedule in 1 ms — RRC assignment happens asynchronously.
        NS_LOG_DEBUG("RegisterRntiMobilityPoll: some RNTIs still 0 — retrying in 1 ms");
        Simulator::Schedule(MilliSeconds(1),
                              &RegisterRntiMobilityPoll,
                              ueNetDev);
    }
}

// ---------------------------------------------------------------------------
// InstallMcdFeedbackGate
// ---------------------------------------------------------------------------

void
InstallMcdFeedbackGate(const NetDeviceContainer& ueNetDev, double mcdMeters)
{
    NS_LOG_FUNCTION(mcdMeters);

    if (mcdMeters >= 1e8)
    {
        NS_LOG_INFO("McdFeedbackGate: mcdMeters=" << mcdMeters
                    << " >= 1e8 — gate disabled");
        return;
    }

    for (uint32_t i = 0; i < ueNetDev.GetN(); ++i)
    {
        auto dev = DynamicCast<NrUeNetDevice>(ueNetDev.Get(i));
        if (!dev)
            continue;

        Ptr<MobilityModel> rxMob = dev->GetNode()->GetObject<MobilityModel>();
        if (!rxMob)
        {
            NS_LOG_WARN("InstallMcdFeedbackGate: node "
                        << dev->GetNode()->GetId()
                        << " has no MobilityModel — skipping");
            continue;
        }

        const uint8_t numBwps = dev->GetCcMapSize();
        for (uint8_t bwpId = 0; bwpId < numBwps; ++bwpId)
        {
            Ptr<NrUePhy> uePhy = dev->GetPhy(bwpId);
            if (!uePhy)
                continue;

            Ptr<NrSpectrumPhy> specPhy = uePhy->GetSpectrumPhy();
            if (!specPhy)
                continue;

            // Build shim — ref-counted via Ptr<>.
            Ptr<McdGateShim> shim = CreateObject<McdGateShim>();
            shim->Setup(rxMob, mcdMeters, uePhy);

            // Re-bind the spectrum phy callback.
            specPhy->SetPhySlHarqFeedbackCallback(
                MakeCallback(&McdGateShim::OnFeedback, shim));

            NS_LOG_INFO("McdFeedbackGate: installed on node "
                        << dev->GetNode()->GetId()
                        << " bwp=" << +bwpId
                        << " mcd=" << mcdMeters << "m");
        }
    }
}

} // namespace ns3
