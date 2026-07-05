#pragma once
// nr_sl_stack.h
//
// Builds the NR + NR-Sidelink stack on a NodeContainer and applies the
// ThreeGppGeometricNlosvModel wrapper to every BWP immediately after
// NrHelper::InitializeOperationBand() so the geometry-aware channel is
// active for all subsequent device installation and simulation.
//
// The base 3GPP channel scenario (default V2V_Urban) is chosen by the
// caller via SlStackParams::scenario — this is the primary way to move
// away from the LOS V2V_Highway model used by groupcast_eval.

#include "common/channel/geometric_channel.h"

#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/nr-module.h"
#include "ns3/point-to-point-epc-helper.h"

#include <cstdint>
#include <list>
#include <map>
#include <string>

namespace ns3
{

struct SlStackParams
{
    uint32_t mu{1};
    uint32_t bwPRBs{50};
    uint32_t mcs{14};
    uint32_t seed{1};
    uint8_t  maxNumTx{5};
    uint16_t psfchPeriod{0};
    bool     shadowingEnabled{true};
    BandwidthPartInfo::Scenario scenario{BandwidthPartInfo::V2V_Urban};
    // When true (default), TX node 0 gets NrSlAdaptiveMcsScheduler.
    // Set false for fixed-MCS experiments so --mcs is honoured on the TX node.
    bool adaptiveMcs{true};
    // Allowed RRI values (ms) advertised in the resource pool.
    // Empty = use the full NR-V2X set: {0} ∪ {1..99} ∪ {100,200,...,1000}.
    std::list<uint16_t> rriList{};
    // Per-destination fixed-MCS overrides {dstL2Id → mcs}. When non-empty, TX
    // node 0 gets a NrSlAdaptiveMcsScheduler whose SetDstMcsOverride() pins each
    // listed destination to a fixed MCS (dual-MCS redundant groupcast). The
    // override bypasses all adaptive logic, so MCS stays fixed regardless of
    // the adaptiveMcs flag.
    std::map<uint32_t, uint8_t> dstMcsOverride{};

    // Per-node MCS override (mcs applied at the scheduler on the listed nodeIds).
    // For 2-hop same-L2ID relay: leader = mcsHop1, relayNode = mcsHop2.
    // Empty = every node uses the global mcs (current default behaviour).
    // Applied AFTER the default NrSlUeMacSchedulerFixedMcs is installed on all
    // UEs, by replacing the scheduler on the listed node with a fresh
    // NrSlUeMacSchedulerFixedMcs at the per-node MCS value. Affects only the
    // listed TX nodes (RNTI); RX-only nodes are unaffected.
    std::map<uint32_t, uint8_t> nodeMcsOverride{};

    // -- Deterministic (collision-free) scheduler --
    // When "deterministic", every UE gets NrSlDeterministicScheduler instead of
    // SB-SPS: node 0 (leader) is assigned partition lane 0 and every other UE
    // (relay candidates) lane 1, so leader and relay transmissions never share a
    // subchannel/slot (p_col -> 0). Any other value keeps the default SB-SPS.
    std::string schedulerMode{"sbsps"};   // "sbsps" | "deterministic"
    std::string detScheme{"fdm"};         // "fdm" (subchannel lanes) | "tdm" (slot phases)
    uint8_t     detNumLanes{2};           // number of disjoint partitions
    uint8_t     detTotalSubch{10};        // subchannels in the pool (for FDM split)

    // -- Explicit bandwidth control --
    // By default the bandwidth is derived as 200 * 2^mu (100-kHz units), which
    // gives 80 MHz at mu=2. Set channelBwMHz > 0 to override this and specify
    // the SLRP bandwidth explicitly (e.g. 20 MHz at mu=2). The matching
    // subchannel size is bwPRBs (as before); numSubchannels controls how many
    // subchannels the pool sees. When numSubchannels == 0 (default) the pool
    // uses bwPRBs as the subchannel size and auto-derives the count from the
    // band width (legacy behaviour). When numSubchannels > 0 AND channelBwMHz >
    // 0, the subchannel size is derived as floor(availablePRBs / numSubchannels)
    // and bwPRBs is overridden for the pool (bwPRBs is still used for the
    // scheduler default if no override is made by the caller).
    //
    // For the relay_storm_eval target: mu=2, channelBwMHz=20, numSubchannels=10
    // → ~51 PRBs available at mu=2/20 MHz → subchannel size = 5 PRBs.
    double   channelBwMHz{0.0};    //!< 0 = legacy (200<<mu); >0 = explicit MHz
    uint32_t numSubchannels{0};    //!< 0 = legacy (pool uses bwPRBs); >0 = explicit

    // -- HARQ feedback option --
    // 1 = GroupcastOnlyNack (3GPP Option-1, NACK-only feedback).
    //     BuildSlStack installs the MCR feedback gate so out-of-range
    //     receivers are silent (spec mechanism for Option-1).
    // 2 = Groupcast (3GPP Option-2, ACK/NACK feedback).
    //     BuildSlStack does NOT install the MCR feedback gate. Spec uses
    //     Zone-ID membership (sl-ZoneConfig) for Option-2, not distance;
    //     in our single-dstL2Id setup with all UEs as group members, the
    //     spec-faithful behavior is no distance filter.
    // Default 1 preserves backward compatibility with prior campaigns that
    // ran with HARQ Option-1 semantics; set to 2 for HARQ Option-2 setups.
    uint8_t harqOption{1};

    // -- MCR feedback gate (HARQ Option-1 only) --
    // When harqOption == 1 AND mcrMeters > 0, BuildSlStack calls
    // InstallMcrFeedbackGate() on all UE devices, suppressing PSFCH from
    // receivers farther than mcrMeters from the TB's transmitter.
    // This implements the spec's MCR distance gate for Option-1 (out-of-range
    // receivers are silent, treated as NACK by the sender).
    // Ignored when harqOption == 2 (no distance filter per spec).
    // 0.0 = disabled (Option-1 with no gate; full feedback from all RX nodes).
    double mcrMeters{0.0};

    // -- PSFCH feedback KPI (HARQ Option-2 / Unicast measurement) --
    // When recordPsfchKpi == true, BuildSlStack installs the PsfchKpiShim on
    // every UE's sender-side PSFCH callback, recording ACK/NACK arrivals per
    // (sender, dstL2Id, harqPid, rx) and emitting SILENCE rows when a PSFCH
    // opportunity closes without feedback from an expected group member.
    // See common/psfch_kpi/psfch_kpi.h for full semantics.
    // This is a measurement-only module — it does not gate or modify feedback.
    // CastType filtering: Broadcast dstL2Ids are skipped; Option-1 NACK-only
    // is out of scope for v1; Option-2 Groupcast and Unicast are recorded.
    // CSV is written at Simulator::Destroy via ScheduleDestroy.
    bool        recordPsfchKpi{false};
    std::string psfchKpiPath;       //!< Base CSV path. Empty = no write.
    bool        psfchKpiSummaryOnly{false}; //!< Skip per-event CSV; emit summary only.
};

struct SlStackCtx
{
    Ptr<NrPointToPointEpcHelper> epc;
    Ptr<NrHelper>                nr;
    Ptr<NrSlHelper>              sl;
    NetDeviceContainer           ueNetDev;
    Ipv4InterfaceContainer       ueIpIface;
    int64_t                      stream{0};
    std::map<uint16_t, uint32_t> rntiToNode; //!< RNTI → nodeId (for future CSI work)
};

SlStackCtx BuildSlStack(const SlStackParams& p,
                        const GeomChannelCfg& geom,
                        const NodeContainer& nodes);

} // namespace ns3
