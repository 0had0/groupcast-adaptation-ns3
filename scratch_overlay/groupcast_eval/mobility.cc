// mobility.cc

#include "mobility.h"

#include "ns3/constant-position-mobility-model.h"
#include "ns3/constant-velocity-mobility-model.h"
#include "ns3/log.h"
#include "ns3/mobility-helper.h"
#include "ns3/rng-seed-manager.h"
#include "ns3/vector.h"

#ifdef NS3_TRACI
#include "ns3/traci-client.h"
#endif

#include <memory>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("GroupcastEval.Mobility");

NodeContainer
InstallMobility(const MobConfig& cfg)
{
    NodeContainer nodes;
    nodes.Create(cfg.numUEs);

#ifdef NS3_TRACI
    if (!cfg.sumoCfg.empty())
    {
        // Park every node far off-map; TraCI will assign real positions once
        // SUMO vehicles enter the simulation.
        MobilityHelper mob;
        mob.SetMobilityModel("ns3::ConstantPositionMobilityModel");
        mob.Install(nodes);
        for (uint32_t i = 0; i < nodes.GetN(); ++i)
        {
            nodes.Get(i)
                ->GetObject<ConstantPositionMobilityModel>()
                ->SetPosition(Vector(-1e5 + i * 10.0, -1e5, 0.0));
        }

        Ptr<TraciClient> traci = CreateObject<TraciClient>();
        traci->SetAttribute("SumoConfigPath", StringValue(cfg.sumoCfg));
        traci->SetAttribute("SumoBinaryPath", StringValue(cfg.sumoBinary));
        traci->SetAttribute("SynchInterval", TimeValue(Seconds(cfg.sumoSyncInterval)));
        traci->SetAttribute("StartTime", TimeValue(Seconds(0)));
        traci->SetAttribute("SumoGUI", BooleanValue(false));
        traci->SetAttribute("SumoPort", UintegerValue(3400));
        traci->SetAttribute("PenetrationRate", DoubleValue(1.0));
        traci->SetAttribute("SumoLogFile", BooleanValue(false));
        traci->SetAttribute("SumoStepLog", BooleanValue(false));
        traci->SetAttribute("SumoSeed", IntegerValue(static_cast<int>(cfg.seed)));
        traci->SetAttribute("SumoWaitForSocket", TimeValue(Seconds(1)));

        NodeContainer pool = nodes;
        auto cursor = std::make_shared<uint32_t>(0);
        STARTUP_FCN includeNode = [pool, cursor](std::string) -> Ptr<Node> {
            if (*cursor >= pool.GetN())
            {
                return nullptr;
            }
            return pool.Get((*cursor)++);
        };
        SHUTDOWN_FCN excludeNode = [](Ptr<Node>, std::string) {};
        traci->SumoSetup(includeNode, excludeNode);
        NS_LOG_INFO("SUMO mobility: " << cfg.sumoCfg);
        return nodes;
    }
#endif

    // --- Highway fallback: single lane, constant velocity at 100 km/h -----
    MobilityHelper mob;
    mob.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
    mob.Install(nodes);

    const double interVehicleDist = 20.0; // metres
    const double speed = 27.78;           // m/s ≈ 100 km/h
    const double antennaHeight = 1.6;     // metres (3GPP TR 37.885 §6.1.2)

    for (uint32_t i = 0; i < nodes.GetN(); ++i)
    {
        auto m = nodes.Get(i)->GetObject<ConstantVelocityMobilityModel>();
        m->SetPosition(Vector(i * interVehicleDist, 0.0, antennaHeight));
        m->SetVelocity(Vector(speed, 0.0, 0.0));
    }

    NS_LOG_INFO("Highway mobility: " << nodes.GetN()
                                      << " UEs, spacing=" << interVehicleDist
                                      << " m, speed=" << speed << " m/s");
    return nodes;
}

} // namespace ns3
