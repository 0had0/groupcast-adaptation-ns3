// common/mcd/mcr_feedback_gate.cc
//
// Implementation of the castType-aware feedback gate — see mcr_feedback_gate.h.
//
// Design note on callback re-binding:
//   NrSlHelper::PrepareUeForSidelink() wires:
//     spectrumPhy->SetPhySlHarqFeedbackCallback(
//         MakeCallback(&NrUePhy::EnqueueSlHarqFeedback, nrUePhy));
//   We re-call SetPhySlHarqFeedbackCallback() with a shim that:
//     - is an ns3::Object subclass so it participates in ns3's Ptr<> ref-counting
//     - captures (rxMobility, mcrMeters, originalUePhy)
//     - resolves TX mobility from the global RntiMobilityRegistry
//     - resolves castType from the global DstL2IdCastTypeRegistry
//     - applies per-castType rules:
//       * Broadcast: drop ALL feedback (spec: no PSFCH for broadcast)
//       * Groupcast (Option-2): forward unchanged (spec: Zone-ID, not distance;
//         in single-dstL2Id setups all UEs are members, so no gate is spec-faithful)
//       * Unicast: forward unchanged
//       * GroupcastOnlyNack (Option-1): apply MCR distance gate
//   MakeCallback(&McrGateShim::OnFeedback, shimPtr) gives us a valid
//   Callback<void, const SlHarqInfo&> without any std::function.
//
// Spec applicability: the gate is installed by BuildSlStack only when
// harqOption == 1. For harqOption == 2, no gate is installed (spec uses
// Zone-ID membership for Option-2; in our single-dstL2Id deployments all
// UEs are members so spec-faithful behavior is no gate).

#include "mcr_feedback_gate.h"

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

NS_LOG_COMPONENT_DEFINE("McrFeedbackGate");

// CastType values — must match NrSlSciF2aHeader::CastTypeIndicator_t
// (ns-3-dev/contrib/nr/model/nr-sl-sci-f2a-header.h).
static constexpr uint8_t CAST_BROADCAST        = 0;
static constexpr uint8_t CAST_GROUPCAST        = 1; // Option-2 ACK/NACK
static constexpr uint8_t CAST_UNICAST          = 2;
static constexpr uint8_t CAST_GROUPCAST_NACK   = 3; // Option-1 NACK-only

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
    NS_LOG_INFO("McrFeedbackGate: registered rnti=" << rnti
                << " bwp=" << +bwpId);
}

Ptr<MobilityModel>
RntiMobilityRegistry::Lookup(uint16_t rnti, uint8_t bwpId) const
{
    auto it = m_map.find({rnti, bwpId});
    if (it == m_map.end())
    {
        NS_LOG_WARN("McrFeedbackGate: rnti=" << rnti << " bwp=" << +bwpId
                    << " not in registry — feedback NOT suppressed (default behavior)");
        return nullptr;
    }
    return it->second;
}

// ---------------------------------------------------------------------------
// DstL2IdCastTypeRegistry
// ---------------------------------------------------------------------------

/* static */
DstL2IdCastTypeRegistry&
DstL2IdCastTypeRegistry::Instance()
{
    static DstL2IdCastTypeRegistry s_instance;
    return s_instance;
}

void
DstL2IdCastTypeRegistry::Register(uint32_t dstL2Id, uint8_t castType)
{
    m_map[dstL2Id] = castType;
    NS_LOG_INFO("McrFeedbackGate: registered dstL2Id=" << dstL2Id
                << " castType=" << +castType);
}

uint8_t
DstL2IdCastTypeRegistry::Lookup(uint32_t dstL2Id) const
{
    auto it = m_map.find(dstL2Id);
    if (it == m_map.end())
    {
        // Default: Groupcast (Option-2). This matches the most common
        // single-dstL2Id groupcast setup (dstL2Id=254). Callers should
        // register explicitly for broadcast / nack-only / unicast flows.
        return CAST_GROUPCAST;
    }
    return it->second;
}

void
RegisterDstL2IdCastType(uint32_t dstL2Id, uint8_t castType)
{
    DstL2IdCastTypeRegistry::Instance().Register(dstL2Id, castType);
}

// ---------------------------------------------------------------------------
// McrGateShim: ns3::Object so Ptr<> ref-counting is safe.
// Wraps one UE's spectrum-phy feedback slot. Applies castType-aware and
// distance-aware gating per the rules in mcr_feedback_gate.h.
// ---------------------------------------------------------------------------

class McrGateShim : public Object
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::McrGateShim")
                                .SetParent<Object>()
                                .SetGroupName("NrV2xCommon");
        return tid;
    }

    void Setup(Ptr<MobilityModel> rxMob,
               double mcrMeters,
               Ptr<NrUePhy> uePhy)
    {
        m_rxMob     = rxMob;
        m_mcrMeters = mcrMeters;
        m_uePhy     = uePhy;
    }

    // This is the re-bound callback — invoked by NrSpectrumPhy in place of
    // NrUePhy::EnqueueSlHarqFeedback.
    void OnFeedback(const SlHarqInfo& info)
    {
        const uint8_t castType =
            DstL2IdCastTypeRegistry::Instance().Lookup(info.m_dstL2Id);

        // Per-castType rule 1: Broadcast → drop ALL feedback (spec).
        if (castType == CAST_BROADCAST)
        {
            NS_LOG_DEBUG("McrFeedbackGate: suppressing broadcast feedback"
                         << " txRnti=" << info.m_txRnti
                         << " dstL2Id=" << info.m_dstL2Id
                         << " (spec: broadcast has no PSFCH feedback)");
            return;
        }

        // Per-castType rule 2/3: Groupcast (Option-2) or Unicast → forward
        // unchanged. Spec uses Zone-ID membership for Groupcast (we don't
        // model it; in single-dstL2Id setups all UEs are members so no
        // filter is spec-faithful). Unicast has no gate per spec.
        if (castType == CAST_GROUPCAST || castType == CAST_UNICAST)
        {
            NS_LOG_DEBUG("McrFeedbackGate: passing feedback"
                         << " txRnti=" << info.m_txRnti
                         << " dstL2Id=" << info.m_dstL2Id
                         << " castType=" << +castType
                         << " (no distance gate per spec)");
            m_uePhy->EnqueueSlHarqFeedback(info);
            return;
        }

        // Per-castType rule 4: GroupcastOnlyNack (Option-1) → apply MCR
        // distance gate. Drop feedback from out-of-range receivers.
        // (castType is one of CAST_BROADCAST/GROUPCAST/UNICAST/GROUPCAST_NACK;
        //  fall through to MCR gate only for GROUPCAST_NACK.)
        Ptr<MobilityModel> txMob =
            RntiMobilityRegistry::Instance().Lookup(info.m_txRnti, info.m_bwpIndex);

        if (txMob)
        {
            const double dist = m_rxMob->GetDistanceFrom(txMob);
            if (dist > m_mcrMeters)
            {
                NS_LOG_DEBUG("McrFeedbackGate: suppressing Option-1 feedback"
                             << " txRnti=" << info.m_txRnti
                             << " dstL2Id=" << info.m_dstL2Id
                             << " dist=" << dist
                             << "m > mcr=" << m_mcrMeters << "m");
                return; // suppress — out of MCR range (Option-1 spec mechanism)
            }
            NS_LOG_DEBUG("McrFeedbackGate: passing Option-1 feedback"
                         << " txRnti=" << info.m_txRnti
                         << " dstL2Id=" << info.m_dstL2Id
                         << " dist=" << dist
                         << "m <= mcr=" << m_mcrMeters << "m");
        }
        // TX not in registry or within MCR — forward.
        m_uePhy->EnqueueSlHarqFeedback(info);
    }

  private:
    Ptr<MobilityModel> m_rxMob;
    double             m_mcrMeters{1e9};
    Ptr<NrUePhy>       m_uePhy;
};

NS_OBJECT_ENSURE_REGISTERED(McrGateShim);

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
// InstallMcrFeedbackGate
// ---------------------------------------------------------------------------

void
InstallMcrFeedbackGate(const NetDeviceContainer& ueNetDev, double mcrMeters)
{
    NS_LOG_FUNCTION(mcrMeters);

    if (mcrMeters >= 1e8)
    {
        NS_LOG_INFO("McrFeedbackGate: mcrMeters=" << mcrMeters
                    << " >= 1e8 — MCR distance gate disabled (still applies"
                    << " broadcast suppression + castType routing)");
    }

    for (uint32_t i = 0; i < ueNetDev.GetN(); ++i)
    {
        auto dev = DynamicCast<NrUeNetDevice>(ueNetDev.Get(i));
        if (!dev)
            continue;

        Ptr<MobilityModel> rxMob = dev->GetNode()->GetObject<MobilityModel>();
        if (!rxMob)
        {
            NS_LOG_WARN("InstallMcrFeedbackGate: node "
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
            Ptr<McrGateShim> shim = CreateObject<McrGateShim>();
            shim->Setup(rxMob, mcrMeters, uePhy);

            // Re-bind the spectrum phy callback.
            specPhy->SetPhySlHarqFeedbackCallback(
                MakeCallback(&McrGateShim::OnFeedback, shim));

            NS_LOG_INFO("McrFeedbackGate: installed on node "
                        << dev->GetNode()->GetId()
                        << " bwp=" << +bwpId
                        << " mcr=" << mcrMeters << "m");
        }
    }
}

} // namespace ns3
