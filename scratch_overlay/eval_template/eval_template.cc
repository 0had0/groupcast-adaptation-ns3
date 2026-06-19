// eval_template/eval_template.cc
//
// NR-V2X evaluation template — copy to a new scratch dir and customise.
//
// Composition model:
//   1. MobConfig + InstallMobility()             → scratch_common / mobility
//   2. SlStackParams + BuildSlStack()            → scratch_common / stack
//   3. vector<FlowDef> + InstallGroupcastFlows() → scratch_common / flows
//   4. KpiHooks + ConnectPhyMacTraces()
//             + ConnectAppTraces()               → scratch_common / kpi
//   5. Simulator::Run() + Flush()
//
// All of these are pulled from the scratch_common static library.
// Module docs: scratch_overlay/common/README.md
//
// To create a new simulation:
//   ./scripts/new-simulation.sh my_new_eval
//   # → scratch_overlay/my_new_eval/  (copy of this template)
//   #   ns-3-dev/scratch/my_new_eval  (symlink, ready to build)
//   ./ns3 build scratch/my_new_eval/my_new_eval
//   ./ns3 run  scratch/my_new_eval/my_new_eval -- --numUEs=8 --simTime=10

#include "common/channel/geometric_channel.h"
#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
#include "common/mobility/mobility.h"
#include "common/stack/nr-sl-adaptive-mcs-controller.h"  // header-only
#include "common/stack/nr-sl-adaptive-mcs-scheduler.h"
#include "common/stack/nr_sl_stack.h"
#include "common/kpi/v2x-kpi.h"

#include "ns3/core-module.h"
#include "ns3/log.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("EvalTemplate");

int main(int argc, char* argv[])
{
    // -----------------------------------------------------------------------
    // 1. CLI parameters
    // -----------------------------------------------------------------------
    uint32_t numUEs   = 8;
    uint32_t seed     = 1;
    double   simTime  = 10.0;  // seconds of application traffic
    double   warmup   = 2.0;   // seconds before KPI measurement starts
    uint32_t mu       = 1;     // NR numerology (0=15kHz, 1=30kHz)
    uint32_t bwPRBs   = 50;    // subchannel size in PRBs
    uint32_t mcs      = 14;    // fixed MCS index (ignored when adaptiveMcs=true)
    uint32_t maxNumTx = 3;     // HARQ retransmission limit (1 = no retx)
    uint16_t psfchPeriod = 0;  // PSFCH period in slots (0 = disabled)
    bool     harq     = true;
    std::string sumoCfg;       // path to .sumocfg (required for SUMO mobility)
    std::string sumoBinary = "sumo";
    std::string kpiDb = "eval_template";      // output DB basename
    std::string flows;                        // empty → DefaultGroupcastFlows()

    CommandLine cmd(__FILE__);
    cmd.AddValue("numUEs",      "Number of UEs in the group",              numUEs);
    cmd.AddValue("seed",        "RNG seed",                                seed);
    cmd.AddValue("simTime",     "Traffic duration (s)",                    simTime);
    cmd.AddValue("warmup",      "Pre-KPI warm-up period (s)",              warmup);
    cmd.AddValue("mu",          "NR numerology",                           mu);
    cmd.AddValue("bwPRBs",      "Subchannel size in PRBs",                 bwPRBs);
    cmd.AddValue("mcs",         "Fixed MCS index",                         mcs);
    cmd.AddValue("maxNumTx",    "Max PSSCH TX count (1 = no HARQ)",        maxNumTx);
    cmd.AddValue("psfchPeriod", "PSFCH period in slots (0 = disabled)",    psfchPeriod);
    cmd.AddValue("harq",        "Enable HARQ (true/false)",                harq);
    cmd.AddValue("sumoCfg",     "SUMO .sumocfg path (empty = no SUMO)",    sumoCfg);
    cmd.AddValue("sumoBinary",  "SUMO binary path",                        sumoBinary);
    cmd.AddValue("kpiDb",       "Output SQLite DB basename (no .db)",      kpiDb);
    cmd.AddValue("flows",       "Flow spec override (empty = 3-flow mix)", flows);
    cmd.Parse(argc, argv);

    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(seed);

    // -----------------------------------------------------------------------
    // 2. Mobility
    // -----------------------------------------------------------------------
    MobConfig mobCfg;
    mobCfg.numUEs           = numUEs;
    mobCfg.seed             = seed;
    mobCfg.sumoCfg          = sumoCfg;
    mobCfg.sumoBinary       = sumoBinary;
    mobCfg.sumoSyncInterval = 0.1;
    // To use static platoon placement instead of SUMO:
    //   mobCfg.staticPlatoon = true;
    //   mobCfg.ivd           = 20.0;   // inter-vehicle distance (m)
    //   mobCfg.staticFrontX  = 1000.0;

    MobResult mob = InstallMobility(mobCfg);
    NodeContainer& ueNodes = mob.ueNodes;

    // -----------------------------------------------------------------------
    // 3. Geometry-aware NLOSv channel configuration
    // -----------------------------------------------------------------------
    GeomChannelCfg geomCfg;
    if (mob.traci)
    {
        geomCfg.traci = mob.traci;  // live SUMO: blocker positions from TraCI
    }
    else
    {
        geomCfg.blockerNodes = ueNodes;  // static/trace mode: all nodes block
    }

    // -----------------------------------------------------------------------
    // 4. NR + Sidelink stack
    //
    // BuildSlStack() applies the geometric NLOSv channel to every BWP and
    // installs NrSlAdaptiveMcsScheduler on TX node 0 when adaptiveMcs=true.
    // Set adaptiveMcs=false and configure stackParams.mcs for fixed-MCS runs.
    // -----------------------------------------------------------------------
    SlStackParams stackParams;
    stackParams.mu          = mu;
    stackParams.bwPRBs      = bwPRBs;
    stackParams.mcs         = static_cast<uint8_t>(mcs);
    stackParams.seed        = seed;
    stackParams.maxNumTx    = static_cast<uint8_t>(maxNumTx);
    stackParams.psfchPeriod = psfchPeriod;
    stackParams.adaptiveMcs = false;  // change to true for NACK-rate adaptive MCS

    SlStackCtx stack = BuildSlStack(stackParams, geomCfg, ueNodes);

    // -----------------------------------------------------------------------
    // 5. Groupcast flows
    // -----------------------------------------------------------------------
    std::vector<FlowDef> flowDefs =
        flows.empty() ? DefaultGroupcastFlows() : ParseFlowsSpec(flows);

    double txAppDuration = simTime;
    FlowResult flowResult = InstallGroupcastFlows(
        ueNodes, stack, flowDefs, txAppDuration, harq, stack.stream);

    // -----------------------------------------------------------------------
    // 6. KPI hooks (SQLite DB + V2xKpi)
    // -----------------------------------------------------------------------
    std::string dbPath  = kpiDb + ".db";
    std::string csvPath = kpiDb + "_packets.csv";

    KpiHooks hooks(dbPath, csvPath);
    hooks.v2xKpi.SetDbPath(kpiDb);
    hooks.v2xKpi.SetTxAppDuration(txAppDuration);
    hooks.v2xKpi.SetRangeForV2xKpis(1000);  // 1000-metre range gate

    ConnectPhyMacTraces(hooks);
    ConnectAppTraces(hooks, flowResult);

    // -----------------------------------------------------------------------
    // 7. Run
    // -----------------------------------------------------------------------
    double totalTime = warmup + simTime + 0.5;  // +0.5 s drain margin
    Simulator::Stop(Seconds(totalTime));
    Simulator::Run();
    Simulator::Destroy();

    Flush(hooks);

    NS_LOG_UNCOND("Done. Results → " << dbPath);
    return 0;
}
