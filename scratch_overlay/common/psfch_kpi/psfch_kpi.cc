// common/psfch_kpi/psfch_kpi.cc
//
// Implementation of the per-PSFCH-opportunity ACK/NACK/SILENCE KPI collector.
// See psfch_kpi.h for design notes.

#include "psfch_kpi.h"

#include "ns3/object.h" // MUST be included before any ns3 module header
#include "ns3/log.h"
#include "ns3/net-device.h"
#include "ns3/node.h"
#include "ns3/nr-spectrum-phy.h" // defines NrPhyRxSlPsfchCallback
#include "ns3/nr-ue-net-device.h"
#include "ns3/nr-ue-phy.h"
#include "ns3/simulator.h"

#include "common/mcd/mcr_feedback_gate.h" // for DstL2IdCastTypeRegistry + CastType values

#include <algorithm>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <tuple>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("PsfchKpi");

// CastType values — must match NrSlSciF2aHeader::CastTypeIndicator_t
// (ns-3-dev/contrib/nr/model/nr-sl-sci-f2a-header.h) and the constants in
// mcr_feedback_gate.cc. Only the ones we filter on are referenced.
static constexpr uint8_t CAST_BROADCAST        = 0;
[[maybe_unused]] static constexpr uint8_t CAST_GROUPCAST        = 1; // Option-2 ACK/NACK
[[maybe_unused]] static constexpr uint8_t CAST_UNICAST          = 2;
static constexpr uint8_t CAST_GROUPCAST_NACK   = 3; // Option-1 NACK-only

// PSFCH event status codes (also the CSV column values).
static constexpr uint8_t STATUS_ACK     = 0;
static constexpr uint8_t STATUS_NACK    = 1;
static constexpr uint8_t STATUS_SILENCE = 2;

// ---------------------------------------------------------------------------
// PsfchKpiCollector
// ---------------------------------------------------------------------------

/* static */
PsfchKpiCollector&
PsfchKpiCollector::Instance()
{
    static PsfchKpiCollector s_instance;
    return s_instance;
}

void
PsfchKpiCollector::RegisterDstL2IdMembers(uint32_t dstL2Id,
                                          const std::set<uint32_t>& nodeIds)
{
    m_members[dstL2Id] = nodeIds;
    NS_LOG_INFO("PsfchKpi: registered dstL2Id=" << dstL2Id
                << " members=" << nodeIds.size());
}

void
PsfchKpiCollector::CloseOpportunity(const std::tuple<uint32_t, uint32_t, uint8_t>& key)
{
    auto it = m_currentOpp.find(key);
    if (it == m_currentOpp.end())
    {
        return;
    }

    const auto& [senderNodeId, dstL2Id, harqPid] = key;
    const OppState& opp = it->second;

    // Look up expected RX set (members - sender).
    auto mit = m_members.find(dstL2Id);
    if (mit != m_members.end())
    {
        for (uint32_t rx : mit->second)
        {
            if (rx == senderNodeId)
            {
                continue; // sender doesn't send feedback to itself
            }
            if (opp.rxStatus.find(rx) == opp.rxStatus.end())
            {
                PsFchEvent ev;
                ev.simTime_s     = opp.simTime_s;
                ev.sfn           = opp.sfn;
                ev.senderNodeId  = senderNodeId;
                ev.rxNodeId      = rx;
                ev.dstL2Id       = dstL2Id;
                ev.harqPid       = harqPid;
                ev.status        = STATUS_SILENCE;
                m_events.push_back(ev);
            }
        }
    }

    m_currentOpp.erase(it);
}

void
PsfchKpiCollector::RecordArrival(uint32_t senderNodeId,
                                 uint32_t rxNodeId,
                                 const SlHarqInfo& info,
                                 double simTime_s,
                                 uint64_t sfn)
{
    // CastType filter: only Option-2 (Groupcast) and Unicast are in scope.
    // Option-1 NACK-only and Broadcast are skipped.
    const uint8_t castType =
        DstL2IdCastTypeRegistry::Instance().Lookup(info.m_dstL2Id);
    if (castType == CAST_BROADCAST || castType == CAST_GROUPCAST_NACK)
    {
        return; // out of scope for v1
    }

    const uint8_t harqPid = info.m_harqProcessId;
    const auto key = std::make_tuple(senderNodeId, info.m_dstL2Id, harqPid);

    auto it = m_currentOpp.find(key);
    if (it == m_currentOpp.end())
    {
        // First arrival for this tuple — start a new opportunity.
        OppState opp;
        opp.sfn = sfn;
        opp.simTime_s = simTime_s;
        opp.rxStatus[rxNodeId] = info.IsReceivedOk() ? STATUS_ACK : STATUS_NACK;
        m_currentOpp.emplace(key, opp);
    }
    else if (it->second.sfn == sfn)
    {
        // Same opportunity — append (first arrival wins).
        if (it->second.rxStatus.find(rxNodeId) == it->second.rxStatus.end())
        {
            it->second.rxStatus[rxNodeId] =
                info.IsReceivedOk() ? STATUS_ACK : STATUS_NACK;
        }
    }
    else
    {
        // New opportunity has begun for this tuple — close the previous one
        // (which emits SILENCE for any expected_rx that didn't respond),
        // then start a fresh opp.
        CloseOpportunity(key);
        OppState opp;
        opp.sfn = sfn;
        opp.simTime_s = simTime_s;
        opp.rxStatus[rxNodeId] = info.IsReceivedOk() ? STATUS_ACK : STATUS_NACK;
        m_currentOpp.emplace(key, opp);
    }

    // Always append the ACK/NACK event itself.
    PsFchEvent ev;
    ev.simTime_s     = simTime_s;
    ev.sfn           = sfn;
    ev.senderNodeId  = senderNodeId;
    ev.rxNodeId      = rxNodeId;
    ev.dstL2Id       = info.m_dstL2Id;
    ev.harqPid       = harqPid;
    ev.status        = info.IsReceivedOk() ? STATUS_ACK : STATUS_NACK;
    m_events.push_back(ev);
}

void
PsfchKpiCollector::ScheduleCsvWrite(const std::string& basePath, bool summaryOnly)
{
    if (basePath.empty())
    {
        NS_LOG_WARN("PsfchKpi: empty CSV path — skipping write");
        return;
    }
    Simulator::ScheduleDestroy(&PsfchKpiCollector::WriteCsv,
                               this,
                               basePath,
                               summaryOnly);
}

void
PsfchKpiCollector::WriteCsv(const std::string& basePath, bool summaryOnly) const
{
    // Close any still-open opportunities first — emit SILENCE for those.
    // (const_cast needed because CloseOpportunity mutates m_currentOpp.)
    auto& self = const_cast<PsfchKpiCollector&>(*this);
    for (auto it = self.m_currentOpp.begin(); it != self.m_currentOpp.end();)
    {
        auto key = it->first;
        ++it;
        self.CloseOpportunity(key);
    }

    // Per-event CSV.
    if (!summaryOnly)
    {
        std::ofstream out(basePath);
        if (!out.is_open())
        {
            NS_LOG_ERROR("PsfchKpi: cannot open " << basePath);
            return;
        }
        out << "sim_time_s,sender_node,dst_l2id,harq_pid,rx_node,status,sfn\n";
        out << std::fixed << std::setprecision(9);
        for (const auto& ev : m_events)
        {
            out << ev.simTime_s << ','
                << ev.senderNodeId << ','
                << ev.dstL2Id << ','
                << static_cast<uint32_t>(ev.harqPid) << ','
                << ev.rxNodeId << ','
                << static_cast<uint32_t>(ev.status) << ','
                << ev.sfn << '\n';
        }
        out.close();
        NS_LOG_INFO("PsfchKpi: wrote " << m_events.size() << " events to "
                    << basePath);
    }

    // Summary CSV: per (sender, dst_l2id, harq_pid) aggregate.
    std::map<std::tuple<uint32_t, uint32_t, uint8_t>,
             std::tuple<uint32_t, uint32_t, uint32_t, double, double>> agg;
    // key → (n_ack, n_nack, n_silence, first_time, last_time)

    for (const auto& ev : m_events)
    {
        auto key = std::make_tuple(ev.senderNodeId, ev.dstL2Id, ev.harqPid);
        auto ait = agg.find(key);
        if (ait == agg.end())
        {
            agg[key] = std::make_tuple(0u, 0u, 0u, ev.simTime_s, ev.simTime_s);
            ait = agg.find(key);
        }
        auto& [nack, nnack, nsil, first_t, last_t] = ait->second;
        if (ev.status == STATUS_ACK)
        {
            ++nack;
        }
        else if (ev.status == STATUS_NACK)
        {
            ++nnack;
        }
        else
        {
            ++nsil;
        }
        if (ev.simTime_s < first_t)
        {
            first_t = ev.simTime_s;
        }
        if (ev.simTime_s > last_t)
        {
            last_t = ev.simTime_s;
        }
    }

    std::ofstream sout(basePath + ".summary.csv");
    if (!sout.is_open())
    {
        NS_LOG_ERROR("PsfchKpi: cannot open " << basePath << ".summary.csv");
        return;
    }
    sout << "sender_node,dst_l2id,harq_pid,n_ack,n_nack,n_silence,"
            "ack_rate,nack_rate,silence_rate,first_psfch_slot_s,last_psfch_slot_s\n";
    sout << std::fixed << std::setprecision(6);
    for (const auto& [key, counts] : agg)
    {
        const auto& [sender, dst, pid] = key;
        const auto& [nack, nnack, nsil, first_t, last_t] = counts;
        const uint32_t total = nack + nnack + nsil;
        const double ackRate = total ? static_cast<double>(nack) / total : 0.0;
        const double nackRate = total ? static_cast<double>(nnack) / total : 0.0;
        const double silRate = total ? static_cast<double>(nsil) / total : 0.0;
        sout << sender << ',' << dst << ',' << static_cast<uint32_t>(pid) << ','
             << nack << ',' << nnack << ',' << nsil << ','
             << ackRate << ',' << nackRate << ',' << silRate << ','
             << first_t << ',' << last_t << '\n';
    }
    sout.close();
    NS_LOG_INFO("PsfchKpi: wrote " << agg.size()
                << " (sender,dst,pid) summaries to " << basePath << ".summary.csv");
}

void
PsfchKpiCollector::ResetForTest()
{
    m_events.clear();
    m_members.clear();
    m_currentOpp.clear();
}

// ---------------------------------------------------------------------------
// PsfchKpiShim: re-binds the sender's PSFCH reception callback to record
// arrivals (then forwards to the original callback).
// ---------------------------------------------------------------------------

class PsfchKpiShim : public Object
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::PsfchKpiShim")
                                .SetParent<Object>()
                                .SetGroupName("NrV2xCommon");
        return tid;
    }

    void Setup(Ptr<NrUePhy> uePhy, uint32_t senderNodeId)
    {
        m_uePhy = uePhy;
        m_senderNodeId = senderNodeId;
    }

    void OnFeedback(uint32_t sendingNodeId, const SlHarqInfo& info)
    {
        // Filter out non-sender UEs: a UE's spectrum phy receives EVERY PSFCH
        // signal on the channel (because the channel is shared), but only the
        // TB-sender's MAC actually processes the feedback (see
        // NrSlUeMac::DoReceivePsfch, which checks m_txRnti == GetRnti()).
        // Non-sender UEs see the callback fire but their MAC drops it. We
        // mirror that filter here so the KPI only records events that
        // actually reach a MAC HARQ state machine.
        if (!m_uePhy || m_uePhy->GetRnti() != info.m_txRnti)
        {
            if (m_original)
            {
                m_original(sendingNodeId, info);
            }
            return;
        }

        const double simTime_s = Simulator::Now().GetSeconds();
        // PSFCH opportunity index. PSFCH events for a given PSSCH arrive
        // within a few microseconds of each other (the channel propagation
        // delay across a 100m platoon is sub-microsecond). The next PSSCH's
        // PSFCH arrives at least one slMinTimeGapPsfch slots later.
        //
        // For mu=2 (60kHz SCS), slot = 0.25ms. With psfchPeriod=4 the gap
        // between opps is 1ms. We bucket arrivals into 1ms windows — this
        // matches psfchPeriod=4 (typical) and may over-bucket for
        // psfchPeriod=1, which is acceptable (over-bucketing reduces
        // reported silence, never increases it).
        constexpr double kOppBucketUs = 1000.0; // 1 ms
        const uint64_t sfn =
            static_cast<uint64_t>(simTime_s * 1e6 / kOppBucketUs);
        PsfchKpiCollector::Instance().RecordArrival(
            m_senderNodeId, sendingNodeId, info, simTime_s, sfn);
        if (m_original)
        {
            m_original(sendingNodeId, info);
        }
    }

    void SetOriginal(NrSpectrumPhy::NrPhyRxSlPsfchCallback cb)
    {
        m_original = cb;
    }

  private:
    Ptr<NrUePhy> m_uePhy;
    uint32_t     m_senderNodeId{0};
    NrSpectrumPhy::NrPhyRxSlPsfchCallback m_original;
};

NS_OBJECT_ENSURE_REGISTERED(PsfchKpiShim);

// ---------------------------------------------------------------------------
// InstallPsfchKpiArrivals
// ---------------------------------------------------------------------------

void
InstallPsfchKpiArrivals(const NetDeviceContainer& ueNetDev, bool mcrInstalled)
{
    NS_LOG_FUNCTION(mcrInstalled);

    for (uint32_t i = 0; i < ueNetDev.GetN(); ++i)
    {
        auto dev = DynamicCast<NrUeNetDevice>(ueNetDev.Get(i));
        if (!dev)
        {
            continue;
        }

        const uint8_t numBwps = dev->GetCcMapSize();
        for (uint8_t bwpId = 0; bwpId < numBwps; ++bwpId)
        {
            Ptr<NrUePhy> uePhy = dev->GetPhy(bwpId);
            if (!uePhy)
            {
                continue;
            }
            Ptr<NrSpectrumPhy> specPhy = uePhy->GetSpectrumPhy();
            if (!specPhy)
            {
                continue;
            }

            // Build shim and capture the original callback.
            // The original is the callback currently bound to the spectrum
            // phy's RX-PSFCH slot. nr-sl-helper.cc:228 wires it to
            // NrUePhy::PhyPsfchReceived; we re-bind to PsfchKpiShim::OnFeedback
            // which records the arrival and forwards to PhyPsfchReceived.
            //
            // NOTE: McrGateShim installs on SetPhySlHarqFeedbackCallback (the
            // RX-UE side, where the receiver enqueues its own feedback to
            // send), NOT on SetNrPhyRxSlPsfchCallback (the sender-side, where
            // the sender receives feedback from RX). These are independent
            // callbacks, so KPI and MCR can be installed together without
            // interfering. mcrInstalled is recorded for log clarity.
            Ptr<PsfchKpiShim> shim = CreateObject<PsfchKpiShim>();
            shim->Setup(uePhy, dev->GetNode()->GetId());

            NrSpectrumPhy::NrPhyRxSlPsfchCallback orig =
                MakeCallback(&NrUePhy::PhyPsfchReceived, uePhy);
            shim->SetOriginal(orig);

            specPhy->SetNrPhyRxSlPsfchCallback(
                MakeCallback(&PsfchKpiShim::OnFeedback, shim));

            NS_LOG_INFO("PsfchKpi: installed on node "
                        << dev->GetNode()->GetId()
                        << " bwp=" << +bwpId
                        << " mcrInstalled=" << (mcrInstalled ? "true" : "false"));
        }
    }
}

} // namespace ns3