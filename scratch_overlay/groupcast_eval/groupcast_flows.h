#pragma once
// groupcast_flows.h
//
// Defines the three V2X traffic profiles that share one groupcast destination
// (dstL2Id = 254, multicast 225.63.63.1), activates their SL-DRBs, and
// installs OnOff + PacketSink applications on every group UE so that every
// node both transmits and receives on all three flows.
//
// Flow table (fixed; reflects the 3GPP V2X traffic mix from TS 22.186):
//
//  Flow │ Label        │ Port │ Prio │ RRI    │ Sched │ PDB    │ Rate
//  ─────┼──────────────┼──────┼──────┼────────┼───────┼────────┼──────────
//   1   │ URLLC CAM   │ 8001 │  1   │ 20 ms  │  DG   │ 20 ms  │ 200 kb/s
//   2   │ V2X video   │ 8002 │  2   │ 50 ms  │  SPS  │ 50 ms  │ 1000 kb/s
//   3   │ Non-GBR     │ 8003 │  3   │ 100 ms │  SPS  │ 100 ms │ 100 kb/s
//
// Pattern follows sl-multi-lc-example.cc:567-821.

#include "nr_sl_stack.h"

#include "ns3/applications-module.h"
#include "ns3/nstime.h"

#include <array>
#include <cstdint>

namespace ns3
{

static constexpr int kNumFlows = 3;

// Static traffic-profile descriptor for one flow.
struct FlowDef
{
    uint16_t port;         // UDP destination port (also used as flow identifier in CSV)
    uint8_t priority;      // SL logical-channel priority (1 = highest)
    Time rri;              // Resource Reservation Interval
    bool dynamic;          // true = DG (dynamic grant), false = SPS (semi-persistent)
    Time pdb;              // Packet Delay Budget
    double rateKbps;       // OnOff application data rate
    const char* label;     // human-readable tag for logs
};

// The three flows defined above. Declared extern so kpi_hooks.cc can read
// port numbers without re-declaring them.
extern const FlowDef kFlows[kNumFlows];

// Timing information returned after application installation so that
// V2xKpi::SetTxAppDuration and Simulator::Stop can be configured correctly.
struct FlowResult
{
    ApplicationContainer clients[kNumFlows]; // OnOff TX apps (one per UE × flow)
    ApplicationContainer servers[kNumFlows]; // PacketSink RX apps (one per UE × flow)
    double appStart[kNumFlows]{};            // earliest real TX start per flow (s)
    double appStop[kNumFlows]{};             // latest real TX stop per flow (s)
};

// Activate SL-DRBs for all three flows and install TX/RX apps on every node.
// `simTime` is the per-flow traffic duration after the first packet is sent.
// `stream` is advanced in-place to keep random-stream assignments deterministic.
FlowResult InstallGroupcastFlows(const NodeContainer& nodes,
                                 const SlStackCtx& ctx,
                                 double simTime,
                                 bool harqEnabled,
                                 int64_t& stream);

} // namespace ns3
