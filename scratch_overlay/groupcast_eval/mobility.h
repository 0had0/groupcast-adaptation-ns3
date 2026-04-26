#pragma once
// mobility.h
//
// Installs mobility models on a freshly-created NodeContainer.
// Two modes:
//   SUMO  — drives each node's position via TraCI (requires ns3::TraciClient).
//   Highway — constant-velocity single-lane fallback (no external dependency).

#include "ns3/node-container.h"

#include <cstdint>
#include <string>

namespace ns3
{

struct MobConfig
{
    uint32_t numUEs{8};
    uint32_t seed{1};
    std::string sumoCfg;       // path to .sumocfg; empty → use highway fallback
    std::string sumoBinary{"sumo"};
    double sumoSyncInterval{0.1}; // seconds
};

// Creates numUEs nodes, installs positions/velocities, and returns the
// container. When sumoCfg is non-empty and TraCI is available the simulation
// will connect to SUMO at startup; otherwise a straight single-lane highway
// layout at 100 km/h with 20-metre spacing is used.
NodeContainer InstallMobility(const MobConfig& cfg);

} // namespace ns3
