// mobility.cc — SUMO/TraCI or pre-recorded waypoint trace.

#include "mobility.h"

#include "ns3/boolean.h"
#include "ns3/constant-position-mobility-model.h"
#include "ns3/constant-velocity-mobility-model.h"
#include "ns3/double.h"
#include "ns3/integer.h"
#include "ns3/log.h"
#include "ns3/mobility-helper.h"
#include "ns3/string.h"
#include "ns3/uinteger.h"
#include "ns3/waypoint-mobility-model.h"

#include <fstream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <cctype>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("CsiGroupcastEval.Mobility");

// ---------------------------------------------------------------------------
// Trace-file mode: read CSV produced by export_fcd_trace.py and install
// WaypointMobilityModel on each node.
//
// CSV format (header line required):
//   time,node_id,x,y,speed
//   0.0,0,10.5,20.3,5.2
//   ...
// node_id is 0-based sequential index (tx_0→0, rx_1→1, …)
// ---------------------------------------------------------------------------
static MobResult
InstallMobilityFromTrace(const MobConfig& cfg)
{
    std::ifstream f(cfg.mobilityTraceCsv);
    if (!f.is_open())
        NS_ABORT_MSG("mobility trace file not found: " << cfg.mobilityTraceCsv);

    MobResult result;
    result.traci = nullptr;

    NodeContainer nodes;
    nodes.Create(cfg.numUEs);

    // Install WaypointMobilityModel on every node.
    for (uint32_t i = 0; i < nodes.GetN(); ++i)
    {
        auto wmm = CreateObject<WaypointMobilityModel>();
        nodes.Get(i)->AggregateObject(wmm);
    }

    std::string line;

    uint32_t rows = 0;
    while (std::getline(f, line))
    {
        if (line.empty() || line[0] == '#')
            continue;

        // skip header rows (first non-comment line may be a header like
        // "time,node_id,x,y,speed") — detect by checking the first
        // non-space character and ensuring it looks numeric.
        auto pos = line.find_first_not_of(" \t");
        if (pos == std::string::npos)
            continue;
        char c = line[pos];
        if (!(std::isdigit(static_cast<unsigned char>(c)) || c == '-' || c == '.'))
        {
            NS_LOG_WARN("mobility trace: skipping header or malformed row: " << line);
            continue;
        }

        std::istringstream ss(line);
        std::string field;
        double t; uint32_t nid; double x, y, spd;
        try
        {
            std::getline(ss, field, ','); t   = std::stod(field);
            std::getline(ss, field, ','); nid = static_cast<uint32_t>(std::stoul(field));
            std::getline(ss, field, ','); x   = std::stod(field);
            std::getline(ss, field, ','); y   = std::stod(field);
            std::getline(ss, field, ','); spd = std::stod(field);
        }
        catch (...)
        {
            NS_LOG_WARN("mobility trace: skipping malformed row: " << line);
            continue;
        }

        if (nid >= nodes.GetN())
            continue;

        auto wmm = nodes.Get(nid)->GetObject<WaypointMobilityModel>();
        if (wmm)
        {
            wmm->AddWaypoint(Waypoint(Seconds(t), Vector(x, y, 1.5)));
            (void)spd; // speed implicit from consecutive waypoints
        }
        ++rows;
    }

    NS_LOG_INFO("Mobility trace loaded: " << cfg.mobilityTraceCsv
                << "  rows=" << rows << "  nodes=" << cfg.numUEs);

    result.ueNodes = nodes;
    return result;
}

// ---------------------------------------------------------------------------
// SUMO/TraCI mode (default).
// ---------------------------------------------------------------------------
static MobResult
InstallMobilityFromSumo(const MobConfig& cfg)
{
    if (cfg.sumoCfg.empty())
    {
        NS_ABORT_MSG("csi_groupcast_eval: --sumoCfg must not be empty. "
                     "Provide a SUMO .sumocfg path.");
    }

    MobResult result;

    NodeContainer nodes;
    nodes.Create(cfg.numUEs);

    // Park every node off-map; SUMO/TraCI will assign real positions when
    // the corresponding SUMO vehicle enters the simulation.
    MobilityHelper mob;
    mob.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mob.Install(nodes);
    for (uint32_t i = 0; i < nodes.GetN(); ++i)
    {
        nodes.Get(i)
            ->GetObject<ConstantPositionMobilityModel>()
            ->SetPosition(Vector(-1e5 + static_cast<double>(i) * 10.0, -1e5, 0.0));
    }

    // Build and start TraciClient.
    Ptr<TraciClient> traci = CreateObject<TraciClient>();
    traci->SetAttribute("SumoConfigPath",    StringValue(cfg.sumoCfg));
    traci->SetAttribute("SumoBinaryPath",    StringValue(cfg.sumoBinary));
    traci->SetAttribute("SynchInterval",     TimeValue(Seconds(cfg.sumoSyncInterval)));
    traci->SetAttribute("StartTime",         TimeValue(Seconds(0)));
    traci->SetAttribute("SumoGUI",           BooleanValue(false));
    traci->SetAttribute("SumoPort",          UintegerValue(3400));
    traci->SetAttribute("PenetrationRate",   DoubleValue(1.0));
    traci->SetAttribute("SumoLogFile",       BooleanValue(false));
    traci->SetAttribute("SumoStepLog",       BooleanValue(false));
    traci->SetAttribute("SumoSeed",          IntegerValue(static_cast<int>(cfg.seed)));
    traci->SetAttribute("SumoWaitForSocket", TimeValue(Seconds(1)));

    NodeContainer pool = nodes;
    auto cursor = std::make_shared<uint32_t>(0);
    const std::vector<std::string> ueIds = cfg.ueVehicleIds;

    STARTUP_FCN includeNode = [pool, cursor, ueIds](std::string sumoId) -> Ptr<Node> {
        if (!ueIds.empty())
        {
            auto it = std::find(ueIds.begin(), ueIds.end(), sumoId);
            if (it == ueIds.end())
                return nullptr;
            uint32_t idx = static_cast<uint32_t>(std::distance(ueIds.begin(), it));
            return (idx < pool.GetN()) ? pool.Get(idx) : nullptr;
        }
        if (*cursor >= pool.GetN())
            return nullptr;
        return pool.Get((*cursor)++);
    };

    SHUTDOWN_FCN excludeNode = [](Ptr<Node>, std::string) {};

    traci->SumoSetup(includeNode, excludeNode);

    NS_LOG_INFO("SUMO mobility: cfg=" << cfg.sumoCfg << "  numUEs=" << cfg.numUEs);

    result.ueNodes = nodes;
    result.traci   = traci;
    return result;
}

// ---------------------------------------------------------------------------
// Static platoon mode: fixed positions on one lane, no SUMO/TraCI, no trace.
// Node 0 is the front of the platoon (largest x); node i sits ivd metres
// behind node i-1. All nodes share y = staticLaneY, so the platoon is exactly
// collinear and every intermediate vehicle is an NLOSv blocker for any pair
// straddling it.
//
// Moving variant: when cfg.platoonSpeedMps > 0, ConstantVelocityMobilityModel
// is used instead. All nodes share velocity (+x, platoonSpeedMps m/s) so IVD
// spacing is preserved and the channel model sees real Doppler.
// ---------------------------------------------------------------------------
static MobResult
InstallMobilityStatic(const MobConfig& cfg)
{
    MobResult result;
    result.traci = nullptr;

    NodeContainer nodes;
    nodes.Create(cfg.numUEs);

    const bool moving = (cfg.platoonSpeedMps > 0.0);

    if (moving)
    {
        // Install ConstantVelocityMobilityModel on every node.
        MobilityHelper mob;
        mob.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
        mob.Install(nodes);

        for (uint32_t i = 0; i < nodes.GetN(); ++i)
        {
            const double x = cfg.staticFrontX - static_cast<double>(i) * cfg.ivd;
            auto cvm = nodes.Get(i)->GetObject<ConstantVelocityMobilityModel>();
            cvm->SetPosition(Vector(x, cfg.staticLaneY, cfg.antennaHeight));
            cvm->SetVelocity(Vector(cfg.platoonSpeedMps, 0.0, 0.0));
            NS_LOG_INFO("Moving node " << i
                        << " at (" << x << ", " << cfg.staticLaneY
                        << ", " << cfg.antennaHeight << ")"
                        << " v=" << cfg.platoonSpeedMps << " m/s");
        }
    }
    else
    {
        MobilityHelper mob;
        mob.SetMobilityModel("ns3::ConstantPositionMobilityModel");
        mob.Install(nodes);

        for (uint32_t i = 0; i < nodes.GetN(); ++i)
        {
            const double x = cfg.staticFrontX - static_cast<double>(i) * cfg.ivd;
            auto cpm = nodes.Get(i)->GetObject<ConstantPositionMobilityModel>();
            cpm->SetPosition(Vector(x, cfg.staticLaneY, cfg.antennaHeight));
            NS_LOG_INFO("Static node " << i
                        << " at (" << x << ", " << cfg.staticLaneY
                        << ", " << cfg.antennaHeight << ")");
        }
    }

    result.ueNodes = nodes;
    return result;
}

// ---------------------------------------------------------------------------
// Public entry point — dispatches to static, trace, or SUMO mode.
// ---------------------------------------------------------------------------
MobResult
InstallMobility(const MobConfig& cfg)
{
    if (cfg.staticPlatoon)
        return InstallMobilityStatic(cfg);
    if (!cfg.mobilityTraceCsv.empty())
        return InstallMobilityFromTrace(cfg);
    return InstallMobilityFromSumo(cfg);
}

} // namespace ns3
