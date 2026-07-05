#pragma once
// common/psfch_kpi/psfch_kpi.h
//
// Per-PSFCH-opportunity ACK/NACK/SILENCE KPI collector for NR-V2X sidelink.
//
// Scope (v1):
//   - HARQ Option-2 (Groupcast ACK/NACK) only. NACK-only Option-1 is OUT OF
//     SCOPE: the KPI does not record NACKs for castType=GroupcastOnlyNack.
//   - Broadcast dstL2Ids are skipped (spec has no PSFCH feedback for broadcast).
//   - Unicast dstL2Ids are recorded (spec has full ACK/NACK feedback).
//
// Shim order (independent paths):
//   The KPI re-binds SetNrPhyRxSlPsfchCallback (sender-side reception).
//   The MCR gate re-binds SetPhySlHarqFeedbackCallback (RX-UE enqueue).
//   These are DIFFERENT callbacks on the spectrum phy and operate on
//   different ends of the feedback chain:
//
//     RX UE receives PSSCH (TB)
//       → NrSpectrumPhy::RxSlPssch → m_phySlHarqFeedbackCallback
//         → [MCR-installed] McrGateShim::OnFeedback   ← MCR gate (RX-UE side)
//           → NrUePhy::EnqueueSlHarqFeedback → m_slHarqFbList
//             → SlFeedback → StartTxSlFeedback → channel
//
//     Sender's spectrum phy receives PSFCH signal
//       → NrSpectrumPhy::RxSlPsfch → m_nrPhyRxSlPsfchCallback
//         → [KPI-installed] PsfchKpiShim::OnFeedback ← KPI shim (sender-side RX)
//           → NrUePhy::PhyPsfchReceived → MAC HARQ state
//
//   MCR filters what the RX UE transmits (drops feedback from out-of-range
//   RX in Option-1). KPI records what arrives at the sender. For Option-2
//   (Groupcast), MCR is a no-op so KPI sees everything. For Option-1 (NACK-
//   only), MCR drops out-of-range NACKs, so KPI only counts what survived
//   the gate. This is exactly the "post-gate" view that future Option-1 NACK
//   counting would need.
//
// SILENCE semantics:
//   Silence is the absence of ACK or NACK for a (sender, dstL2Id, harqPid, rx)
//   tuple at the END of a PSFCH opportunity — i.e., when a new PSFCH opportunity
//   for that tuple begins and the previous one had no entry for that RX.
//
//   Implementation: PsfchKpiCollector keeps a per-tuple "current opportunity"
//   state (sfn, simTime, map<rxNodeId, ACK/NACK>). On each arrival:
//     - if no current opp → start one with this slot's events;
//     - if current opp's sfn matches the new slot → append;
//     - if current opp's sfn is from a previous slot → CLOSE the previous opp
//       (emit SILENCE for every expected_rx not seen), then start fresh.
//
//   contrib/nr has no PSFCH error model, so the only paths to silence are:
//     (a) RX-side enqueue drop (Option-1 MCR gate at sender-of-feedback side —
//         NOT a path for Option-2);
//     (b) MinTimeGapPsfch timing (RX hasn't reached the PSFCH opportunity yet);
//     (c) RX UE has nothing to send (no PSSCH decoded this slot).
//   For Option-2 / single-dstL2Id setups (no gate behavior), silence rate
//   should be ~0; any silence observed is path (b) or (c).
//
// No edits to contrib/nr. All hooks use existing public callback setters.

#include "ns3/net-device-container.h"
#include "ns3/nr-phy-mac-common.h"
#include "ns3/ptr.h"

#include <cstdint>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace ns3
{

// ---------------------------------------------------------------------------
// Event row written to the per-event CSV.
// One row per (sender, dstL2Id, harqPid, rx) per PSFCH opportunity.
// status: 0=ACK, 1=NACK, 2=SILENCE.
// ---------------------------------------------------------------------------
struct PsFchEvent
{
    double   simTime_s{0.0};
    uint64_t sfn{0}; // SfnSf::Normalize() — frame*subframe*slot
    uint32_t senderNodeId{0};
    uint32_t rxNodeId{0};
    uint32_t dstL2Id{0};
    uint8_t  harqPid{0};
    uint8_t  status{0};
};

// ---------------------------------------------------------------------------
// Per-tuple current-opportunity state.
// Key = (senderNodeId, dstL2Id, harqPid). Value accumulates arrivals for
// the in-progress PSFCH opportunity (one sfn).
// ---------------------------------------------------------------------------
struct OppState
{
    uint64_t sfn{0};                   // sfn of the opportunity currently open
    double   simTime_s{0.0};           // sim time when the opportunity was opened
    std::map<uint32_t, uint8_t> rxStatus; // rxNodeId → 0=ACK, 1=NACK (first arrival wins)
};

// ---------------------------------------------------------------------------
// Singleton collector. Lives for the whole sim; events are appended to
// m_events and dumped to CSV at end-of-sim (or on demand).
// ---------------------------------------------------------------------------
class PsfchKpiCollector
{
  public:
    static PsfchKpiCollector& Instance();

    // Record an ACK or NACK arrival for a (sender, dstL2Id, harqPid, rx) tuple.
    // Called from PsfchKpiShim::OnFeedback on every post-gate PSFCH arrival.
    //
    // CastType filter: dstL2Ids whose castType is Broadcast are NOT recorded
    // (spec has no PSFCH feedback for broadcast). Option-2 (Groupcast) and
    // Unicast ARE recorded. Option-1 (GroupcastOnlyNack) is OUT OF SCOPE for v1.
    void RecordArrival(uint32_t senderNodeId,
                       uint32_t rxNodeId,
                       const SlHarqInfo& info,
                       double simTime_s,
                       uint64_t sfn);

    // Register the set of node IDs that participate in a given dstL2Id group.
    // Used to compute expected_rx_set for silence detection.
    // Idempotent — last write wins.
    void RegisterDstL2IdMembers(uint32_t dstL2Id, const std::set<uint32_t>& nodeIds);

    // Schedule the CSV writer at Simulator::Destroy. Called once from
    // BuildSlStack. summaryOnly=true suppresses the per-event CSV.
    void ScheduleCsvWrite(const std::string& basePath, bool summaryOnly);

    // Synchronous CSV write (also called from ScheduleCsvWrite). Safe to call
    // multiple times — last write wins (file is truncated each call).
    void WriteCsv(const std::string& basePath, bool summaryOnly) const;

    // For tests: reset the singleton. Does NOT touch the file system.
    void ResetForTest();

    // For tests: read-only access to recorded events.
    const std::vector<PsFchEvent>& Events() const { return m_events; }

  private:
    PsfchKpiCollector() = default;

    // Close an open opportunity for a tuple: emit SILENCE rows for any
    // expected_rx that did not arrive in this opp.
    void CloseOpportunity(const std::tuple<uint32_t, uint32_t, uint8_t>& key);

    std::vector<PsFchEvent> m_events;
    std::map<uint32_t, std::set<uint32_t>> m_members;
    // Key is (senderNodeId, dstL2Id, harqPid).
    std::map<std::tuple<uint32_t, uint32_t, uint8_t>, OppState> m_currentOpp;
};

// ---------------------------------------------------------------------------
// Free-function installer. Re-binds SetNrPhyRxSlPsfchCallback on every UE
// spectrum PHY so that PsfchKpiShim::OnFeedback fires before forwarding to
// NrUePhy::PhyPsfchReceived.
//
// mcrInstalled is informational (logged) — the MCR gate is on a different
// callback (SetPhySlHarqFeedbackCallback, RX-UE side), so its install order
// does not affect this one. Pass the result of the MCR-install conditional
// for log clarity.
// ---------------------------------------------------------------------------
void InstallPsfchKpiArrivals(const NetDeviceContainer& ueNetDev, bool mcrInstalled);

} // namespace ns3