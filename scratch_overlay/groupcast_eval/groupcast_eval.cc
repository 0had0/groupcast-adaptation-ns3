// groupcast_eval.cc
//
// Thin orchestrator — parses CLI then calls each module in order:
//
//   InstallMobility()        → mobility.h
//   BuildSlStack()           → nr_sl_stack.h
//   InstallGroupcastFlows()  → groupcast_flows.h
//   ConnectPhyMacTraces()  \
//   ConnectAppTraces()      | → kpi_hooks.h
//   Flush()                /
//
// Driven by scratch/adaptive-phy-eval/scripts/run_sweep.py.

#include "common/channel/geometric_channel.h"
#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
#include "common/mobility/mobility.h"
#include "common/stack/nr_sl_stack.h"

#include "ns3/core-module.h"
#include "ns3/log.h"

#include <algorithm>
#include <fstream>
#include <iostream>
#include <stdexcept>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("GroupcastEval");

int
main(int argc, char* argv[])
{
    // -----------------------------------------------------------------------
    // CLI — all flags recognised by run_sweep.py are preserved.
    // -----------------------------------------------------------------------
    std::string runId = "run0";
    uint32_t seed = 1;
    uint32_t numUEs = 8;
    uint32_t mu = 1;
    uint32_t bwPRBs = 50;
    uint32_t mcs = 14;
    double simTime = 10.0;
    double warmup = 1.0;
    std::string outPacketsCsv;
    std::string outRunCsv;
    std::string sumoCfg;
    std::string sumoBinary = "sumo";
    double sumoSync = 0.1;
    std::string outputDir = "./";
    std::string simTag;
    bool harqEnabled = true;
    uint32_t maxNumTx = 5;
    uint32_t psfchPeriod = 0;
    std::string flowsSpec;

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId",         "Unique run identifier",                          runId);
    cmd.AddValue("seed",          "Random seed",                                    seed);
    cmd.AddValue("numUEs",        "Group size N",                                   numUEs);
    cmd.AddValue("mu",            "Numerology (0=15kHz | 1=30kHz | 2=60kHz)",       mu);
    cmd.AddValue("bwPRBs",        "Subchannel size W in PRBs",                      bwPRBs);
    cmd.AddValue("mcs",           "Fixed MCS index",                                mcs);
    cmd.AddValue("simTime",       "Traffic duration per flow (s)",                  simTime);
    cmd.AddValue("warmup",        "Warm-up seconds to discard in post-processing",  warmup);
    cmd.AddValue("outPacketsCsv", "Per-packet CSV output path",                     outPacketsCsv);
    cmd.AddValue("outRunCsv",     "Per-run summary CSV output path",                outRunCsv);
    cmd.AddValue("sumoCfg",       "SUMO .sumocfg path (empty = highway fallback)",  sumoCfg);
    cmd.AddValue("sumoBinary",    "SUMO binary path",                               sumoBinary);
    cmd.AddValue("sumoSync",      "SUMO sync interval (s)",                         sumoSync);
    cmd.AddValue("outputDir",     "Directory for KPI SQLite DB",                    outputDir);
    cmd.AddValue("simTag",        "DB filename tag (default = runId)",              simTag);
    cmd.AddValue("harq",          "Enable HARQ globally (per-flow harq=... overrides)", harqEnabled);
    cmd.AddValue("maxNumTx",      "Max PSSCH transmissions per TB (1..32; 1=no retx)", maxNumTx);
    cmd.AddValue("psfchPeriod",   "PSFCH period in slots (0=off, 1/2/4=on); HARQ feedback only flows when >0", psfchPeriod);
    cmd.AddValue("flows",
                 "Per-flow spec: comma-separated flows, each `;`-separated "
                 "key=value (label,port,prio,rri_ms,pdb_ms,dynamic,rate_kbps,"
                 "harq,enabled). Empty => 3-flow default.",
                 flowsSpec);
    cmd.Parse(argc, argv);

    if (maxNumTx < 1 || maxNumTx > 32)
    {
        std::cerr << "groupcast_eval: --maxNumTx must be in [1, 32]" << std::endl;
        return 1;
    }
    if (psfchPeriod != 0 && psfchPeriod != 1 && psfchPeriod != 2 && psfchPeriod != 4)
    {
        std::cerr << "groupcast_eval: --psfchPeriod must be 0, 1, 2, or 4" << std::endl;
        return 1;
    }

    std::vector<FlowDef> flowDefs;
    try
    {
        flowDefs = flowsSpec.empty() ? DefaultGroupcastFlows()
                                     : ParseFlowsSpec(flowsSpec);
    }
    catch (const std::exception& e)
    {
        std::cerr << "groupcast_eval: --flows parse error: " << e.what() << std::endl;
        return 1;
    }

    if (simTag.empty())
    {
        simTag = runId;
    }
    numUEs = std::max(numUEs, 2u);

    RngSeedManager::SetSeed(1);
    RngSeedManager::SetRun(seed);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    // -----------------------------------------------------------------------
    // 1. Mobility
    // -----------------------------------------------------------------------
    MobConfig mobCfg;
    mobCfg.numUEs = numUEs;
    mobCfg.seed = seed;
    mobCfg.sumoCfg = sumoCfg;
    mobCfg.sumoBinary = sumoBinary;
    mobCfg.sumoSyncInterval = sumoSync;

    MobResult mob = InstallMobility(mobCfg);
    NodeContainer& ueNodes = mob.ueNodes;

    // -----------------------------------------------------------------------
    // 2. NR + sidelink stack (helpers, BWP, pool, preconfig, IP)
    // -----------------------------------------------------------------------
    SlStackParams stackParams;
    stackParams.mu = mu;
    stackParams.bwPRBs = bwPRBs;
    stackParams.mcs = mcs;
    stackParams.seed = seed;
    stackParams.maxNumTx = static_cast<uint8_t>(maxNumTx);
    stackParams.psfchPeriod = static_cast<uint16_t>(psfchPeriod);
    stackParams.adaptiveMcs = false;  // groupcast_eval uses fixed MCS

    // Geometric channel with no blocker geometry (baseline, no NLOSv from vehicles).
    GeomChannelCfg geomCfg;
    if (mob.traci)
    {
        geomCfg.traci = mob.traci;
    }
    else
    {
        geomCfg.blockerNodes = ueNodes;  // static-platoon mode: all nodes are potential blockers
    }

    SlStackCtx stack = BuildSlStack(stackParams, geomCfg, ueNodes);

    // -----------------------------------------------------------------------
    // 3. Groupcast bearer activation + OnOff/PacketSink apps (3 flows)
    // -----------------------------------------------------------------------
    FlowResult flows = InstallGroupcastFlows(ueNodes, stack, flowDefs, simTime,
                                             harqEnabled, stack.stream);

    // -----------------------------------------------------------------------
    // 4. Trace hooks: PHY/MAC → SQLite DB + app-layer → DB + CSV
    // -----------------------------------------------------------------------
    const std::string dbBase = outputDir + simTag;
    KpiHooks hooks(dbBase, outPacketsCsv);

    // Set V2xKpi app duration before connecting traces so it is ready when
    // the first packet event fires.
    double txAppDuration = 0.0;
    for (size_t f = 0; f < flows.defs.size(); ++f)
    {
        txAppDuration = std::max(txAppDuration, flows.appStop[f] - flows.appStart[f]);
    }
    hooks.v2xKpi.SetDbPath(dbBase);
    hooks.v2xKpi.SetTxAppDuration(txAppDuration);
    hooks.v2xKpi.SetRangeForV2xKpis(1000); // 1000-metre range gate for PIR/PRR

    ConnectPhyMacTraces(hooks);
    ConnectAppTraces(hooks, flows);

    // -----------------------------------------------------------------------
    // 5. Run
    // -----------------------------------------------------------------------
    double simStop = 0.0;
    for (size_t f = 0; f < flows.defs.size(); ++f)
    {
        simStop = std::max(simStop, flows.appStart[f] + simTime + 0.5);
    }
    Simulator::Stop(Seconds(simStop));
    Simulator::Run();

    // -----------------------------------------------------------------------
    // 6. Flush: drain DB caches, write V2xKpi tables, close CSV
    // -----------------------------------------------------------------------
    Flush(hooks);

    // -----------------------------------------------------------------------
    // 7. One-line run summary CSV (consumed by aggregate_results.py)
    // -----------------------------------------------------------------------
    if (!outRunCsv.empty())
    {
        std::ofstream r(outRunCsv);
        if (r.is_open())
        {
            r << "runId,seed,numUEs,mu,bwPRBs,mcs,simTime,warmup,harq,maxNumTx,psfchPeriod,kpiDb\n"
              << runId << ',' << seed << ',' << numUEs << ',' << mu << ',' << bwPRBs << ','
              << mcs << ',' << simTime << ',' << warmup << ',' << (harqEnabled ? 1 : 0)
              << ',' << maxNumTx << ',' << psfchPeriod << ',' << dbBase << ".db\n";
        }
    }

    Simulator::Destroy();
    NS_LOG_INFO("Done: " << runId);
    return 0;
}
