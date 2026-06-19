#pragma once
// mobility.h — SUMO/TraCI only.
//
// csi_groupcast_eval requires a SUMO scenario; there is no synthetic highway
// fallback.  The binary will abort at startup if --sumoCfg is not provided.

#include "ns3/node-container.h"
#include "ns3/ptr.h"
#include "ns3/traci-client.h"

#include <cstdint>
#include <string>
#include <vector>

namespace ns3
{

struct MobConfig
{
    uint32_t    numUEs{8};
    uint32_t    seed{1};
    std::string sumoCfg;               //!< path to .sumocfg (required when mobilityTraceCsv is empty)
    std::string sumoBinary{"sumo"};
    double      sumoSyncInterval{0.1}; //!< TraCI sync interval (s)
    // Optional: restrict which SUMO vehicle IDs become UE nodes.
    // When empty the first numUEs vehicles injected by SUMO are used.
    std::vector<std::string> ueVehicleIds;

    // Pre-recorded mobility trace (CSV produced by export_fcd_trace.py).
    // When non-empty, WaypointMobilityModel is used and SUMO/TraCI is NOT started.
    // sumoCfg is still required as a fallback default but ignored in trace mode.
    std::string mobilityTraceCsv;

    // STATIC platoon mode (no SUMO, no trace). When staticPlatoon is true the
    // nodes are placed with ConstantPositionMobilityModel on one lane: node 0 at
    // the front (largest x) and node i at x0 - i*ivd, all at y = staticLaneY.
    // Used by static_relay_collision_eval to reproduce leader<->relay collisions
    // in a controlled geometry. SUMO/TraCI is not started.
    bool   staticPlatoon{false};
    double ivd{20.0};          //!< inter-vehicle distance (m), same lane
    double staticLaneY{0.0};   //!< lane y-coordinate (m)
    double staticFrontX{1000.0}; //!< x of the front node (node 0)
    double antennaHeight{1.5}; //!< node z (m)
};

struct MobResult
{
    NodeContainer    ueNodes;
    Ptr<TraciClient> traci;
};

// Creates numUEs nodes, connects to SUMO via TraCI, and returns both the
// node container and the TraciClient so that the geometric channel model
// can look up blocker vehicle positions during the simulation.
MobResult InstallMobility(const MobConfig& cfg);

} // namespace ns3
