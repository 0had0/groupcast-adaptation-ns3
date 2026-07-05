// common/mcd/mcr_nack_only_gate.cc
//
// Implementation of the MCR NACK-only feedback gate — see mcr_nack_only_gate.h.
//
// Design follows McrFeedbackGate (mcr_feedback_gate.cc): re-bind the spectrum
// PHY PSFCH feedback callback on every UE to a shim that:
//   - resolves TX mobility from the shared RntiMobilityRegistry
//   - drops feedback when distance > MCR (out-of-range)
//   - drops ACK feedback when distance <= MCR AND dropAcks=true (Option 1)
//   - forwards NACK feedback in-range to the original NrUePhy::EnqueueSlHarqFeedback
//
// The shim is an ns3::Object subclass so it participates in Ptr<> ref-counting
// exactly like McrGateShim.

#include "mcr_nack_only_gate.h"

#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/node.h"
#include "ns3/nr-spectrum-phy.h"
#include "ns3/nr-ue-net-device.h"
#include "ns3/nr-ue-phy.h"
#include "ns3/object.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("McrNackOnlyGate");

// ---------------------------------------------------------------------------
// McrNackOnlyShim: ns3::Object so Ptr<> ref-counting is safe.
// Wraps one UE's spectrum-phy PSFCH feedback slot. State captured at install
// time: rx mobility, MCR distance, ACK-drop flag, original NrUePhy (so we can
// forward accepted NACKs to the original callback).
// ---------------------------------------------------------------------------

class McrNackOnlyShim : public Object
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::McrNackOnlyShim")
                                .SetParent<Object>()
                                .SetGroupName("NrV2xCommon");
        return tid;
    }

    void Setup(Ptr<MobilityModel> rxMob,
               double mcrMeters,
               bool dropAcks,
               Ptr<NrUePhy> uePhy)
    {
        m_rxMob     = rxMob;
        m_mcrMeters = mcrMeters;
        m_dropAcks  = dropAcks;
        m_uePhy     = uePhy;
    }

    // Re-bound callback — invoked by NrSpectrumPhy in place of the previous
    // PSFCH feedback callback (which is NrUePhy::EnqueueSlHarqFeedback or
    // McrGateShim::OnFeedback depending on stack order).
    void OnFeedback(const SlHarqInfo& info)
    {
        Ptr<MobilityModel> txMob =
            RntiMobilityRegistry::Instance().Lookup(info.m_txRnti, info.m_bwpIndex);

        if (txMob)
        {
            const double dist = m_rxMob->GetDistanceFrom(txMob);
            if (dist > m_mcrMeters)
            {
                NS_LOG_DEBUG("McrNackOnlyGate: out-of-MCR drop"
                             << " txRnti=" << info.m_txRnti
                             << " dist=" << dist
                             << "m > mcr=" << m_mcrMeters << "m");
                return; // suppress — out of MCR range
            }
        }
        else
        {
            NS_LOG_WARN("McrNackOnlyGate: TX rnti=" << info.m_txRnti
                        << " bwp=" << +info.m_bwpIndex
                        << " not in registry — forwarding unconditionally");
        }

        // In-range. Apply Option-1 ACK-drop semantics if enabled.
        if (m_dropAcks && info.m_harqStatus == SlHarqInfo::ACK)
        {
            NS_LOG_DEBUG("McrNackOnlyGate: dropping ACK (Option-1 silent-on-success)"
                         << " txRnti=" << info.m_txRnti);
            return; // suppress ACK
        }

        // Forward NACK (and ACK if dropAcks=false) to original callback.
        m_uePhy->EnqueueSlHarqFeedback(info);
    }

  private:
    Ptr<MobilityModel> m_rxMob;
    double             m_mcrMeters{1e9};
    bool               m_dropAcks{true};
    Ptr<NrUePhy>       m_uePhy;
};

NS_OBJECT_ENSURE_REGISTERED(McrNackOnlyShim);

// ---------------------------------------------------------------------------
// InstallMcrNackOnlyGate — re-bind the PSFCH feedback callback on every
// UE device to the shim. Same looping pattern as InstallMcrFeedbackGate.
// ---------------------------------------------------------------------------

void
InstallMcrNackOnlyGate(const NetDeviceContainer& ueNetDev,
                       double mcrMeters,
                       bool dropAcks)
{
    NS_LOG_FUNCTION(mcrMeters << dropAcks);

    if (mcrMeters >= 1e8)
    {
        NS_LOG_INFO("McrNackOnlyGate: mcrMeters=" << mcrMeters
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
            NS_LOG_WARN("InstallMcrNackOnlyGate: node "
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

            Ptr<McrNackOnlyShim> shim = CreateObject<McrNackOnlyShim>();
            shim->Setup(rxMob, mcrMeters, dropAcks, uePhy);

            specPhy->SetPhySlHarqFeedbackCallback(
                MakeCallback(&McrNackOnlyShim::OnFeedback, shim));

            NS_LOG_INFO("McrNackOnlyGate: installed on node "
                        << dev->GetNode()->GetId()
                        << " bwp=" << +bwpId
                        << " mcr=" << mcrMeters
                        << "m dropAcks=" << dropAcks);
        }
    }
}

} // namespace ns3