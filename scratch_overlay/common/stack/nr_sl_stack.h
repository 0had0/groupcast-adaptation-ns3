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

    // -- MCD feedback gate --
    // When mcdMeters > 0, InstallMcdFeedbackGate() is called on all UE devices
    // after the SL bearer/pre-config wiring, suppressing PSFCH from receivers
    // farther than mcdMeters from the TB's transmitter.
    // 0.0 = disabled (no gate installed; full feedback from all RX nodes).
    double mcdMeters{0.0};
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
