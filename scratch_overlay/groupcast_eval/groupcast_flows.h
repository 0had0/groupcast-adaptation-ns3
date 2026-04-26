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

#include "nr_sl_stack.h"

#include "ns3/applications-module.h"
#include "ns3/nstime.h"

#include <cstdint>
#include <string>
#include <vector>

namespace ns3
{

// Runtime traffic-profile descriptor for one flow.
struct FlowDef
{
    std::string label;        // human-readable tag (also used as flow key in logs)
    uint16_t port;            // UDP destination port (also used as flow identifier in CSV)
    uint8_t priority;         // SL logical-channel priority (1 = highest, 8 = lowest)
    Time rri;                 // Resource Reservation Interval
    bool dynamic;             // true = DG (dynamic grant), false = SPS (semi-persistent)
    Time pdb;                 // Packet Delay Budget
    double rateKbps;          // OnOff application data rate
    bool enabled;             // false = parsed but skipped at install time
    int8_t harqOverride;      // -1 = use global --harq; 0/1 = per-flow override
};

// Returns the canonical 3-flow mix used when --flows is empty.
std::vector<FlowDef> DefaultGroupcastFlows();

// Parses the --flows CLI string into a validated vector. Throws
// std::runtime_error with a descriptive message on malformed input.
std::vector<FlowDef> ParseFlowsSpec(const std::string& spec);

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
