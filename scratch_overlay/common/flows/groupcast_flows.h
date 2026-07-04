#pragma once
// groupcast_flows.h
//
// Defines V2X traffic profiles that share one groupcast destination
// (dstL2Id = 254, multicast 225.63.63.1), activates their SL-DRBs, and
// installs OnOff + PacketSink applications on every group UE so that every
// node both transmits and receives on every flow.
//
// The flow set is now runtime-configurable via --flows on the CLI; if no
// spec is passed, DefaultGroupcastFlows() returns the canonical 3-flow mix:
//
//  Flow │ Label        │ Port │ Prio │ RRI    │ Sched │ PDB    │ Rate
//  ─────┼──────────────┼──────┼──────┼────────┼───────┼────────┼──────────
//   1   │ urllc_cam   │ 8001 │  1   │ 20 ms  │  DG   │ 20 ms  │ 200 kb/s
//   2   │ v2x_video   │ 8002 │  2   │ 50 ms  │  SPS  │ 50 ms  │ 1000 kb/s
//   3   │ non_gbr     │ 8003 │  3   │ 100 ms │  SPS  │ 100 ms │ 100 kb/s
//
// Optional per-flow keys in --flows:
//   tx_nodes=<id>[:<id>...]  colon-separated list of UE node indices that run
//                            the OnOff TX app for this flow. Missing/empty =>
//                            every UE transmits (today's behaviour). PacketSink
//                            is always installed on every UE so all UEs receive.
//   start_ms=<ms>            absolute simulation-time onset (in milliseconds)
//                            for this flow's OnOff app. Missing => default
//                            bearerActivation (2000 ms) + small jitter.
//   stop_ms=<ms>             absolute simulation-time stop (in milliseconds) for
//                            this flow's OnOff app. Must be > start_ms when both
//                            are set. Missing => app stops at start + simTime
//                            (today's behaviour).

#include "common/stack/nr_sl_stack.h"

#include "ns3/applications-module.h"
#include "ns3/nstime.h"

#include <cstdint>
#include <string>
#include <vector>

namespace ns3
{

// Traffic generator class for a flow.
//   OnOffConstant : NS-3 OnOffApplication at the flow's (rateKbps, rri).
//   Periodic1..3, Aperiodic1..2 : CustomTrafficApplication modes from
//   scratch_overlay/custom-traffic/ — each mode drives its own packet-size
//   and inter-arrival schedule, so rateKbps / rri_ms become informational
//   when a non-OnOff traffic class is selected.
enum class TrafficClass : uint8_t
{
    OnOffConstant = 0,
    Periodic1     = 1, // 100 ms, sizes cycle through {300,190,190,190,190}
    Periodic2     = 2, // 10 ms, 800 B (0.8) or 1200 B (0.2)
    Periodic3     = 3, // 30 ms, uniform 30–60 kB, 10 kB step
    Aperiodic1    = 4, // 50 ms + Exp(50 ms), uniform 200–2000 B, 200 B step
    Aperiodic2    = 5  // 10 ms + Exp(10 ms), uniform 10–30 kB, 4 kB step
};

// HARQ feedback mode selector (additive — defaults preserve original behavior).
//   Default : follow the legacy global --harq flag (harqEnabled -> Groupcast /
//             Broadcast). All existing callers are unaffected.
//   AckNack : explicit Option-2 — same as default when harqEnabled=true
//             (castType=Groupcast). Useful when the caller wants to override
//             the per-flow value regardless of the global flag.
//   NackOnly: Option-1 — castType=GroupcastNegativeOnly. The PHY-level ACK-drop
//             is enforced by common/mcd/mcr_nack_only_gate (installed by the
//             simulation binary on the MCR scope). All existing flows that do
//             not set this field keep their original behavior.
enum class HarqMode : uint8_t
{
    Default = 0,  //!< follow the global --harq flag (legacy)
    AckNack = 1,  //!< explicit Option-2 (castType=Groupcast)
    NackOnly = 2, //!< explicit Option-1 (castType=GroupcastNegativeOnly)
};

// Runtime traffic-profile descriptor for one flow.
struct FlowDef
{
    std::string label;             // human-readable tag (also used as flow key in logs)
    uint16_t port;                 // UDP destination port (also used as flow identifier in CSV)
    uint8_t priority;              // SL logical-channel priority (1 = highest, 8 = lowest)
    Time rri;                      // Resource Reservation Interval
    bool dynamic;                  // true = DG (dynamic grant), false = SPS (semi-persistent)
    Time pdb;                      // Packet Delay Budget
    double rateKbps;               // OnOff application data rate (informational if trafficClass != OnOffConstant)
    bool enabled;                  // false = parsed but skipped at install time
    int8_t harqOverride;           // -1 = use global --harq; 0/1 = per-flow override
    std::vector<uint32_t> txNodes; // empty = every UE transmits this flow
    Time startTime;                // Time(0) sentinel = use bearerActivation default
    Time stopTime;                 // Time(0) sentinel = appStart + simTime (default)
    TrafficClass trafficClass{TrafficClass::OnOffConstant};
    HarqMode harqMode{HarqMode::Default}; //!< HARQ feedback mode (additive; Default = legacy global --harq)

    // -- Split-groupcast extensions --
    //   dstL2Id : NR-SL groupcast L2 destination ID. Default 254 preserves
    //             today's single-groupcast behaviour. Use different values
    //             (e.g. 254 / 255) for multiple parallel groupcasts from the
    //             same TX UE — see scratch_overlay/dual_groupcast_eval/.
    //   rxNodes : list of UE node IDs that install the PacketSink RX app
    //             for this flow. Empty (default) preserves today's behaviour
    //             of installing the sink on every UE so all nodes receive.
    //             When non-empty, only the listed nodes report RX packets in
    //             pktTxRx, effectively partitioning the receiver set per flow.
    uint32_t              dstL2Id{254};
    std::vector<uint32_t> rxNodes{};

    // -- Per-flow fixed MCS --
    //   mcs : -1 (default) = use the global --mcs for this flow's destination.
    //         0..28 = pin this dstL2Id to a fixed MCS via the scheduler's
    //         SetDstMcsOverride() — lets two flows from the same TX UE use two
    //         different MCS (dual-MCS redundant groupcast).
    int16_t               mcs{-1};
};

// Returns the canonical 3-flow mix used when --flows is empty.
std::vector<FlowDef> DefaultGroupcastFlows();

// Parses the --flows CLI string into a validated vector. Throws
// std::runtime_error with a descriptive message on malformed input.
//
// RRI precedence inside a single flow:
//   1. explicit rri_ms=<n>            (always wins)
//   2. DefaultRriForTrafficClass(tc)  (applied when traffic=<tc> is set
//                                      and rri_ms was not explicit)
//   3. 100 ms                         (fallback when neither is set)
std::vector<FlowDef> ParseFlowsSpec(const std::string& spec);

// Per-TrafficClass default Resource Reservation Interval. Returns Time(0)
// for traffic classes that should use dynamic-grant scheduling (i.e. no
// periodic reservation). The 100 ms fallback for OnOffConstant matches the
// historical behaviour. Callers that pick a sentinel of 0 ms with SPS
// scheduling must explicitly flip the flow to dynamic mode.
Time DefaultRriForTrafficClass(TrafficClass tc);

// Per-flow timing/handle information returned after installation.
struct FlowResult
{
    std::vector<FlowDef> defs;                  // mirrors install order (kept for kpi_hooks)
    std::vector<ApplicationContainer> clients;  // OnOff TX apps per flow
    std::vector<ApplicationContainer> servers;  // PacketSink RX apps per flow
    std::vector<double> appStart;               // earliest real TX start per flow (s)
    std::vector<double> appStop;                // latest real TX stop per flow (s)
};

// Activate SL-DRBs for each enabled flow and install TX/RX apps on every node.
// `simTime` is the per-flow traffic duration after the first packet is sent.
// `harqEnabledDefault` is the global --harq value; per-flow harqOverride takes
// precedence when set. `stream` is advanced in place to keep RNG-stream
// assignments deterministic across runs.
FlowResult InstallGroupcastFlows(const NodeContainer& nodes,
                                 const SlStackCtx& ctx,
                                 const std::vector<FlowDef>& flows,
                                 double simTime,
                                 bool harqEnabledDefault,
                                 int64_t& stream);

} // namespace ns3
