// scratch/adaptive-phy-eval/ns3/common/eval-config.cc
// Implementation of EvalConfig command-line parsing.

#include "eval-config.h"

#include "ns3/command-line.h"
#include "ns3/string.h"

namespace ns3::adaptive_phy_eval
{

bool
ParseCommandLine(int argc, char* argv[], EvalConfig& cfg)
{
    CommandLine cmd;

    // Scenario identification
    cmd.AddValue("scenario", "Scenario family (s1, s2, s3, s4)", cfg.scenario);
    cmd.AddValue("condition", "Condition within scenario", cfg.condition);

    // PHY sweep parameters
    cmd.AddValue("numerology", "Numerology μ (0, 1, 2)", cfg.mu);
    cmd.AddValue("bandwidthPrbs", "Bandwidth in PRBs (25, 50, 75, 100)", cfg.bandwidthPrbs);
    cmd.AddValue("mcsIndex", "MCS index", cfg.mcsIndex);

    // Scenario parameters
    cmd.AddValue("numReceivers", "Number of receivers in groupcast", cfg.numReceivers);
    cmd.AddValue("ivdMeters", "Inter-vehicle distance in meters", cfg.ivdMeters);
    cmd.AddValue("speedKmh", "Vehicle speed in km/h", cfg.speedKmh);
    cmd.AddValue("propagation", "Propagation model (highway_los, urban_nlos, mixed)", 
                cfg.propagation);

    // Simulation
    cmd.AddValue("seed", "RNG seed", cfg.seed);
    cmd.AddValue("simDuration", "Simulation duration in seconds", cfg.simDurationS);
    cmd.AddValue("warmup", "Warmup period in seconds", cfg.warmupS);
    cmd.AddValue("packetInterval", "Packet interval in ms", cfg.packetIntervalMs);
    cmd.AddValue("packetSize", "Packet size in bytes", cfg.packetSizeBytes);

    // Paths
    cmd.AddValue("sumoConfig", "Path to SUMO .sumocfg file", cfg.sumoConfigPath);
    cmd.AddValue("outputDir", "Output directory for CSV files", cfg.outputDir);

    bool ok = cmd.Parse(argc, argv);

    if (!ok)
    {
        std::cerr << "Error parsing command-line arguments" << std::endl;
        return false;
    }

    // Validate numerology
    if (cfg.mu > 2)
    {
        std::cerr << "Error: numerology μ must be 0, 1, or 2" << std::endl;
        return false;
    }

    // Validate bandwidth
    if (cfg.bandwidthPrbs != 25 && cfg.bandwidthPrbs != 50 && 
        cfg.bandwidthPrbs != 75 && cfg.bandwidthPrbs != 100)
    {
        std::cerr << "Error: bandwidth must be 25, 50, 75, or 100 PRBs" << std::endl;
        return false;
    }

    // Validate MCS
    if (cfg.mcsIndex > 27)
    {
        std::cerr << "Error: MCS index must be <= 27" << std::endl;
        return false;
    }

    return true;
}

} // namespace ns3::adaptive_phy_eval