#include "ns3/nr-sl-ue-mac-scheduler.h"
#include "common/channel/geometric_channel.h"
#include "common/flows/groupcast_flows.h"
#include "common/mcd/mcr_nack_only_gate.h"

#include "common/kpi/kpi_hooks.h"
#include "common/mobility/mobility.h"
#include "common/relay/fixed_relay_app.h"
#include "common/stack/nr-sl-adaptive-mcs-controller.h"
#include "common/stack/nr-sl-adaptive-mcs-scheduler.h"
#include "common/stack/nr_sl_stack.h"
#include "common/stack/nr_sl_coop_scheduler.h"
#include "common/kpi/v2x-kpi.h"

#include "ns3/core-module.h"
#include "ns3/log.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("highway_kuo_eval");

int main(int argc, char* argv[])
{
    uint32_t numUEs   = 55;
    uint32_t seed     = 1;
    double   simTime  = 5.0;  
    double   warmup   = 2.0;   
    uint32_t mu       = 1;     
    uint32_t bwPRBs   = 50;    
    uint32_t mcs      = 14;    
    uint32_t maxNumTx = 1;     // Kuo evaluates WITHOUT retransmissions from Leader, rely on helper
    uint16_t psfchPeriod = 0;  // 0 = disabled (if no HARQ), or 1 if HARQ
    bool     harq     = false; // We can disable HARQ for the first test
    std::string sumoCfg;       
    std::string mobilityTraceCsv = "scratch/highway_kuo_eval/traces/highway.mob.csv";
    std::string kpiDb = "highway_kuo_eval";      
    std::string flows = "label=leader;port=8001;tx_nodes=0;rxNodes=1:2:3:4:5:6:7:8:9:10:11:12:13:14;dstL2Id=254;traffic=periodic2";
    
    // Relay logic
    uint32_t relayNode = 7;
    uint32_t scenario = 2; // 0=2L, 1=3L, 2=1L+1H, 3=1L+2H, 4=2L+1H, 4=2L+1H
    uint32_t numSubchannels = 4;
    bool staticPlatoon = false;
    double ivd = 20.0;


    CommandLine cmd(__FILE__);
    Config::SetDefault("ns3::NrSlUeMac::EnableChainedRes", BooleanValue(true));
    cmd.AddValue("numUEs",      "Number of UEs",                           numUEs);
    cmd.AddValue("seed",        "RNG seed",                                seed);
    cmd.AddValue("simTime",     "Traffic duration (s)",                    simTime);
    cmd.AddValue("mu",          "NR numerology",                           mu);
    cmd.AddValue("mcs",         "Fixed MCS index",                         mcs);
    cmd.AddValue("maxNumTx",    "Max PSSCH TX count (1 = no HARQ)",        maxNumTx);
    cmd.AddValue("harq",        "Enable HARQ (true/false)",                harq);
    cmd.AddValue("psfchPeriod", "PSFCH Period", psfchPeriod);
    cmd.AddValue("kpiDb",       "Output SQLite DB basename (no .db)",      kpiDb);
    cmd.AddValue("mobilityTraceCsv", "Path to CSV", mobilityTraceCsv);
    cmd.AddValue("flows",       "Flow spec override",                      flows);
    cmd.AddValue("relayNode",   "ID of the chosen helper",                 relayNode);
    cmd.AddValue("scenario", "Scenario", scenario);
    cmd.AddValue("bwPRBs", "Bandwidth in PRBs", bwPRBs);
    cmd.AddValue("numSubchannels", "Number of subchannels", numSubchannels);
    cmd.AddValue("staticPlatoon", "Use static platoon mobility", staticPlatoon);
    cmd.AddValue("ivd", "Inter-vehicle distance", ivd);
    cmd.Parse(argc, argv);

    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(seed);

    MobConfig mobCfg;
    mobCfg.numUEs           = numUEs;
    mobCfg.seed             = seed;
    mobCfg.mobilityTraceCsv = mobilityTraceCsv;
    mobCfg.staticPlatoon = staticPlatoon;
    mobCfg.ivd = ivd;
    if (staticPlatoon) {
        mobCfg.mobilityTraceCsv = "";
    }


    MobResult mob = InstallMobility(mobCfg);
    NodeContainer& ueNodes = mob.ueNodes;

    GeomChannelCfg geomCfg;
    geomCfg.blockerNodes = ueNodes;  // All nodes block (including trucks)

    SlStackParams stackParams;
    stackParams.mu          = mu;
    stackParams.bwPRBs      = bwPRBs;
    stackParams.mcs         = static_cast<uint8_t>(mcs);
    stackParams.seed        = seed;
    stackParams.maxNumTx    = static_cast<uint8_t>(maxNumTx);
    stackParams.maxNumPerReserve = (maxNumTx > 3) ? 3 : static_cast<uint8_t>(maxNumTx);
    stackParams.psfchPeriod = psfchPeriod;
    stackParams.adaptiveMcs = false;  

    
    SlStackCtx stack = BuildSlStack(stackParams, geomCfg, ueNodes);

    // Install CoopScheduler on Leader
    Ptr<NetDevice> dev = ueNodes.Get(0)->GetDevice(0);
    Ptr<NrUeNetDevice> nrDev = dev->GetObject<NrUeNetDevice>();
    Ptr<NrSlUeMac> mac = nrDev->GetMac(0)->GetObject<NrSlUeMac>();
    // Wait, let's just create it:
    Ptr<NrSlCoopScheduler> coopSched = CreateObject<NrSlCoopScheduler>();
    coopSched->SetAttribute("Mcs", UintegerValue(mcs));
    Ptr<NrAmc> perNodeAmc = CreateObject<NrAmc>();
    perNodeAmc->SetAttribute("AmcModel", EnumValue(NrAmc::ErrorModel));
    coopSched->SetNrSlUeMac(mac);
    coopSched->InstallAmc(perNodeAmc);
    
    if (scenario >= 2) {
        coopSched->SetAttribute("ChainedResActive", BooleanValue(true));
        coopSched->SetAttribute("HelperId", UintegerValue(relayNode + 1));
        
        
        
        if (scenario == 4) {
            coopSched->SetAttribute("LeaderTxCount", UintegerValue(2));
            mac->SetAttribute("LeaderTxCount", UintegerValue(2));
        } else {
            coopSched->SetAttribute("LeaderTxCount", UintegerValue(1));
            mac->SetAttribute("LeaderTxCount", UintegerValue(1));
        }
    }
    
    mac->SetAttribute("NrSlUeMacScheduler", PointerValue(coopSched));
    NS_LOG_UNCOND("Installed NrSlCoopScheduler on Leader (Node 0). HelperId=" << relayNode);


    std::vector<FlowDef> flowDefs = ParseFlowsSpec(flows);

    double txAppDuration = simTime;
    FlowResult flowResult = InstallGroupcastFlows(

        ueNodes, stack, flowDefs, txAppDuration, harq, stack.stream);
    //InstallMcrNackOnlyGate(stack.ueNetDev, 1000.0);


    Ptr<FixedRelayApp> relayApp;
    if (false)
    {
        std::vector<uint32_t> tailNodes;
        for (uint32_t n = 1; n < 15; ++n) {
            if (n != relayNode) tailNodes.push_back(n); // Tail is everyone else in platoon
        }

        const uint16_t leaderPort = flowDefs[0].port; // 8001
        const uint16_t relayPort = 8101;
        const uint32_t relayDstL2Id = 255;
        double relayPdb = flowDefs[0].pdb.GetSeconds(); 
        
        Ptr<Application> relayLeaderSink = flowResult.servers[0].Get(relayNode);

        relayApp = InstallFixedRelay(ueNodes, stack, flowResult, relayNode, tailNodes,
                                     relayLeaderSink, leaderPort, relayPort, relayDstL2Id,
                                     Seconds(relayPdb), flowDefs[0].rri, flowDefs[0].dynamic, harq, simTime);
    }

    std::string dbPath  = "scratch/highway_kuo_eval/results/" + kpiDb;
    std::string csvPath = "scratch/highway_kuo_eval/results/" + kpiDb + "_packets.csv";

    KpiHooks hooks(dbPath, csvPath);
    hooks.v2xKpi.SetDbPath("scratch/highway_kuo_eval/results/" + kpiDb);
    hooks.v2xKpi.SetTxAppDuration(txAppDuration);

    if (relayApp) {
        relayApp->SetTimingCb([&hooks](uint32_t seq, uint16_t origPort, uint32_t relayNodeId,
                                       double genSec, double decodeSec, double sendSec, int fwd) {
            // we can log here if needed
        });
    }

    ConnectPhyMacTraces(hooks);
    ConnectAppTraces(hooks, flowResult);

    double totalTime = warmup + simTime + 0.5;  
    Simulator::Stop(Seconds(totalTime));
    Simulator::Run();
    Simulator::Destroy();

    Flush(hooks);

    NS_LOG_UNCOND("Done. Results → " << dbPath);
    return 0;
}
