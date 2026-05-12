/*
 * geometric_channel.h
 *
 * Post-processes an OperationBandInfo (after NrHelper::InitializeOperationBand)
 * and replaces every BWP's channel-condition model with a
 * ThreeGppGeometricNlosvModel wrapper.
 */

#ifndef GEOMETRIC_CHANNEL_H
#define GEOMETRIC_CHANNEL_H

#include "three-gpp-geometric-nlosv.h"

#include "ns3/cc-bwp-helper.h"
#include "ns3/node-container.h"
#include "ns3/ptr.h"
#include "ns3/traci-client.h"

#include <string>
#include <vector>

namespace ns3
{

struct GeomChannelCfg
{
    double              blockerLengthM{5.0};
    double              blockerWidthM{2.5};
    std::vector<std::string> blockerSumoIds{};  // used in SUMO/TraCI mode
    Ptr<TraciClient>    traci{nullptr};
    // In trace-replay mode (no TraCI), supply all nodes as blockers directly.
    // When non-empty, takes precedence over blockerSumoIds + traci.
    NodeContainer       blockerNodes;
};

void ApplyGeometricNlosv(OperationBandInfo& band, const GeomChannelCfg& cfg);

} // namespace ns3

#endif // GEOMETRIC_CHANNEL_H
