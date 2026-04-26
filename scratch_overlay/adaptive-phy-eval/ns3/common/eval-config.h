// scratch/adaptive-phy-eval/ns3/common/eval-config.h
// Configuration struct for adaptive PHY evaluation.
// Extends groupcast_eval::ScenarioConfig with additional parameters.

#ifndef ADAPTIVE_PHY_EVAL_CONFIG_H
#define ADAPTIVE_PHY_EVAL_CONFIG_H

#include "ns3/nstime.h"
#include <cstdint>
#include <string>
#include <utility>

namespace ns3::adaptive_phy_eval
{

/// Configuration for adaptive PHY sweep evaluation.
struct EvalConfig
{
    // === Scenario identification ===
    std::string scenario{"s1"};        // "s1", "s2", "s3", "s4"
    std::string condition{"nrx08"};       // Condition within scenario
    std::string runId{""};              // Unique run ID (auto-generated if empty)

    // === PHY sweep parameters (must match groupcast_eval for compatibility) ===
    uint8_t mu{1};                     // Numerology μ ∈ {0, 1, 2}
    uint16_t bandwidthPrbs{50};          // Bandwidth in PRBs ∈ {25, 50, 75, 100}
    uint8_t mcsIndex{7};                // MCS index ∈ {0,3,5,7,9,11,13,15,17,19,21,23,25,27}

    // === Scenario-specific parameters ===
    uint16_t numReceivers{8};            // N_rx for groupcast
    double ivdMeters{20.0};             // Inter-vehicle distance
    double speedKmh{100.0};            // Vehicle speed in km/h
    std::string propagation{"highway_los"}; // "highway_los", "urban_nlos", "mixed"

    // === Simulation parameters ===
    uint32_t seed{1};                  // RNG seed
    double simDurationS{30.0};           // Simulation duration
    double warmupS{1.0};               // Warmup period
    double packetIntervalMs{100.0};        // CAM interval
    uint32_t packetSizeBytes{300};      // CAM/BSM payload size

    // === IO paths ===
    std::string sumoConfigPath{""};     // Path to SUMO .sumocfg
    std::string outputDir{""};            // Output directory for CSV

    // === Helpers ===

    /// Convert numerology to SCS in kHz
    static uint16_t MuToScs(uint8_t mu)
    {
        return 15 << mu;  // 15 kHz * 2^mu
    }

    /// Convert numerology to slot duration in ms
    static double MuToSlotDurationMs(uint8_t mu)
    {
        return 1.0 / (1 << mu);  // 1ms / 2^mu
    }

    /// Generate runId from parameters if empty
    std::string GetRunId() const
    {
        if (!runId.empty())
            return runId;
        return scenario + "_" + condition + "_mu" + std::to_string(mu) +
               "_w" + std::to_string(bandwidthPrbs) +
               "_mcs" + std::to_string(mcsIndex) +
               "_seed" + std::to_string(seed);
    }

    /// Get output CSV filename
    std::string GetCsvFilename() const
    {
        return outputDir + "/" + GetRunId() + ".csv";
    }
};

/// Parse command-line arguments into EvalConfig.
/// Returns true on success.
bool ParseCommandLine(int argc, char* argv[], EvalConfig& cfg);

} // namespace ns3::adaptive_phy_eval

#endif // ADAPTIVE_PHY_EVAL_CONFIG_H