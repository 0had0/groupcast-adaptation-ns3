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

#include "geometric_channel.h"

#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/nr-module.h"
#include "ns3/point-to-point-epc-helper.h"

#include <cstdint>

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
