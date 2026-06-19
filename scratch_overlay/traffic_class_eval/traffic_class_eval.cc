// traffic_class_eval.cc
//
// Per-RX KPI characterisation across TrafficClass profiles on the
// intersection_nlosv platoon trace, with V2V_Highway pathloss + geometric
// NLOSv overlay (ThreeGppGeometricNlosvModel).
//
// Slim CLI: --trafficClass selects one of
//   periodic1 | periodic2 | periodic3 | aperiodic1 | aperiodic2
// (the CustomTrafficApplication modes defined in
// scratch_overlay/csi_groupcast_eval/groupcast_flows.h).
// --rri overrides the TrafficClass-default Resource Reservation Interval.
//
// Slim CLI: --trafficClass selects one of
//   periodic1 | periodic2 | periodic3 | aperiodic1 | aperiodic2
// (the CustomTrafficApplication modes defined in
// scratch_overlay/csi_groupcast_eval/groupcast_flows.h).
//
// Single flow, single transmitter (node 0). All 9 UEs install a PacketSink
// so every RX is measured by V2xKpi. Outputs a SQLite KPI DB (avrgPrr,
// avrgPir, thput, psschTxStats, psschTbCorruption, plus the raw trace
// tables: pktTxRx, psschTxUeMac, psschRxUePhy, slHarqFeedback, ...).
//
// Reuses all C++ support files from scratch_overlay/csi_groupcast_eval/
// via CMake source-path references — no duplication.

#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
#include "common/mobility/mobility.h"
#include "common/stack/nr_sl_stack.h"
#include "common/kpi/v2x-kpi.h"

#include "ns3/core-module.h"
#include "ns3/log.h"

#include <algorithm>
#include <cctype>
#include <exception>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("TrafficClassEval");

// ---------------------------------------------------------------------------
static TrafficClass
ParseTrafficClass(const std::string& sIn)
{
    std::string s = sIn;
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return std::tolower(c); });

    static const std::map<std::string, TrafficClass> kMap = {
        {"onoff",      TrafficClass::OnOffConstant},
        {"periodic1",  TrafficClass::Periodic1},
        {"periodic2",  TrafficClass::Periodic2},
        {"periodic3",  TrafficClass::Periodic3},
        {"aperiodic1", TrafficClass::Aperiodic1},
        {"aperiodic2", TrafficClass::Aperiodic2},
    };
    auto it = kMap.find(s);
    if (it == kMap.end())
        throw std::runtime_error(
            "--trafficClass must be one of: onoff | periodic1 | periodic2 | "
            "periodic3 | aperiodic1 | aperiodic2 (got '" + sIn + "')");
    return it->second;
}

static BandwidthPartInfo::Scenario
ParseScenario(const std::string& s)
{
    if (s == "V2V_Highway")      return BandwidthPartInfo::V2V_Highway;
    if (s == "V2V_Urban")        return BandwidthPartInfo::V2V_Urban;
    if (s == "UMi_StreetCanyon") return BandwidthPartInfo::UMi_StreetCanyon;
    if (s == "UMa")              return BandwidthPartInfo::UMa;
    throw std::runtime_error("Unknown --baseScenario: " + s);
}

// ---------------------------------------------------------------------------
int
main(int argc, char* argv[])
{
    // ── CLI ────────────────────────────────────────────────────────────────
    std::string trafficClassStr = "periodic2";
    std::string mobilityTrace;            // required (no SUMO fallback)
    std::string baseScenarioStr = "V2V_Highway";
    bool        shadowing       = true;

    uint32_t    txNodeId   = 0;
    uint32_t    numUEs     = 9;
    double      simTime    = 60.0;
    double      warmup     = 2.0;

    uint32_t    mcs         = 14;
    uint32_t    bwPRBs      = 10;
    bool        harqEnabled = true;
    uint32_t    psfchPeriod = 4;
    uint32_t    maxNumTx    = 2;
    uint32_t    mu          = 1;

    uint32_t    seed        = 1;
    uint32_t    rri         = 0; // 0 means use TrafficClass default
    std::string runId       = "run0";
    std::string outputDir   = "./";
    std::string outRunCsv;
    std::string outPacketsCsv;
    std::string simTag;

    double      blockerL    = 4.5;
    double      blockerW    = 2.0;

    CommandLine cmd(__FILE__);
    cmd.AddValue("trafficClass",  "Traffic class: periodic1..3 | aperiodic1..2 | onoff",
                                                                              trafficClassStr);
    cmd.AddValue("mobilityTrace", "Pre-recorded waypoint CSV path (required)",  mobilityTrace);
    cmd.AddValue("baseScenario",  "3GPP scenario: V2V_Highway | V2V_Urban | "
                                  "UMi_StreetCanyon | UMa",                     baseScenarioStr);
    cmd.AddValue("shadowing",     "Enable shadowing in base channel model",    shadowing);
    cmd.AddValue("txNodeId",      "Node ID of the sole transmitter",            txNodeId);
    cmd.AddValue("numUEs",        "Number of UE nodes",                         numUEs);
    cmd.AddValue("simTime",       "Per-flow traffic duration (s)",              simTime);
    cmd.AddValue("warmup",        "Warm-up seconds discarded by V2xKpi",        warmup);
    cmd.AddValue("mcs",           "Fixed MCS index",                            mcs);
    cmd.AddValue("bwPRBs",        "Subchannel size W in PRBs",                  bwPRBs);
    cmd.AddValue("harq",          "Enable HARQ globally",                       harqEnabled);
    cmd.AddValue("psfchPeriod",   "PSFCH period in slots (0/1/2/4)",            psfchPeriod);
    cmd.AddValue("maxNumTx",      "Max PSSCH transmissions per TB (1..32)",     maxNumTx);
    cmd.AddValue("mu",            "Numerology (0=15kHz | 1=30kHz | 2=60kHz)",  mu);
    cmd.AddValue("seed",          "Random seed (rngRun)",                       seed);
    cmd.AddValue("rri",           "Resource Reservation Interval in ms (overrides TrafficClass default)", rri);
    cmd.AddValue("runId",         "Unique run identifier",                      runId);
    cmd.AddValue("outputDir",     "Directory for KPI SQLite DB",                outputDir);
    cmd.AddValue("outRunCsv",     "One-line per-run summary CSV path",          outRunCsv);
    cmd.AddValue("outPacketsCsv", "Per-packet CSV path",                        outPacketsCsv);
    cmd.AddValue("simTag",        "DB filename tag (default = runId)",          simTag);
    cmd.AddValue("blockerL",      "Blocker AABB length (m)",                    blockerL);
    cmd.AddValue("blockerW",      "Blocker AABB width (m)",                     blockerW);
    cmd.Parse(argc, argv);

    // ── Validate ───────────────────────────────────────────────────────────
    if (mobilityTrace.empty())
    {
        std::cerr << "traffic_class_eval: --mobilityTrace is required\n";
        return 1;
    }
    if (maxNumTx < 1 || maxNumTx > 32)
    {
        std::cerr << "traffic_class_eval: --maxNumTx must be in [1,32]\n";
        return 1;
    }
    if (psfchPeriod != 0 && psfchPeriod != 1 &&
        psfchPeriod != 2 && psfchPeriod != 4)
    {
        std::cerr << "traffic_class_eval: --psfchPeriod must be 0, 1, 2, or 4\n";
        return 1;
    }
    if (simTag.empty()) simTag = runId;
    numUEs = std::max(numUEs, 2u);

    TrafficClass tc;
    BandwidthPartInfo::Scenario baseScenario;
    try
    {
        tc           = ParseTrafficClass(trafficClassStr);
        baseScenario = ParseScenario(baseScenarioStr);
    }
    catch (const std::exception& e)
    {
        std::cerr << "traffic_class_eval: " << e.what() << "\n";
        return 1;
    }

    if (txNodeId >= numUEs)
    {
        std::cerr << "traffic_class_eval: --txNodeId (" << txNodeId
                  << ") must be < --numUEs (" << numUEs << ")\n";
        return 1;
    }

    RngSeedManager::SetSeed(1);
    RngSeedManager::SetRun(seed);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    // Validate RRI if explicitly set (non-zero means override TrafficClass default).
    // The ns-3 NrSlCommResourcePoolFactory only accepts:
    //   multiples of 10 ms in [10, 100]   (10, 20, ..., 100)
    //   multiples of 50 ms in [150, 1000] (150, 200, ..., 1000)
    if (rri != 0)
    {
        // Note: 250 ms is excluded — LteRrcSap::GetSlResoResvPrdValue has no MS250 case.
        bool validRri = (rri >= 10 && rri <= 100  && rri % 10 == 0) ||
                        (rri >= 150 && rri <= 1000 && rri % 50 == 0 && rri != 250);
        if (!validRri)
        {
            std::cerr << "traffic_class_eval: --rri must be a multiple of 10 in [10,100] "
                         "or a multiple of 50 in [150,1000] (250 excluded)\n";
            return 1;
        }
    }

    // ── 1. Mobility (trace replay) ─────────────────────────────────────────
    MobConfig mobCfg;
    mobCfg.numUEs           = numUEs;
    mobCfg.seed             = seed;
    mobCfg.mobilityTraceCsv = mobilityTrace;
    // sumoCfg left empty; trace mode ignores it.
    MobResult mob = InstallMobility(mobCfg);
    NodeContainer& ueNodes = mob.ueNodes;

    // ── 2. NR/SL stack + geometric NLOSv (trace mode → all UEs are blockers) ─
    GeomChannelCfg geomCfg;
    geomCfg.blockerLengthM = blockerL;
    geomCfg.blockerWidthM  = blockerW;
    geomCfg.blockerNodes   = ueNodes; // takes precedence over SUMO IDs

    SlStackParams stackParams;
    stackParams.mu               = mu;
    stackParams.bwPRBs           = bwPRBs;
    stackParams.mcs              = mcs;
    stackParams.seed             = seed;
    stackParams.maxNumTx         = static_cast<uint8_t>(maxNumTx);
    stackParams.psfchPeriod      = static_cast<uint16_t>(psfchPeriod);
    stackParams.shadowingEnabled = shadowing;
    stackParams.scenario         = baseScenario;

    SlStackCtx stack = BuildSlStack(stackParams, geomCfg, ueNodes);

    // ── 3. Build a single FlowDef in code (no --flows string parsing) ──────
    // Resolve RRI: explicit --rri wins; otherwise use per-class default.
    Time rriTime = (rri > 0) ? MilliSeconds(static_cast<int64_t>(rri))
                              : DefaultRriForTrafficClass(tc);

    FlowDef fd;
    fd.label        = "tx_only";
    fd.port         = 8001;
    fd.priority     = 3;
    fd.rri          = rriTime;
    fd.dynamic      = (rriTime == Time(0));        // 0 ms sentinel → DG mode
    fd.pdb          = (rriTime > Time(0)) ? rriTime : MilliSeconds(100);
    fd.rateKbps     = 100.0;             // informational for non-OnOff classes
    fd.enabled      = true;
    fd.harqOverride = -1;                // follow global --harq
    fd.txNodes      = { txNodeId };      // only the leader transmits
    fd.startTime    = Time(0);           // default bearer-activation onset
    fd.stopTime     = Time(0);           // default = appStart + simTime
    fd.trafficClass = tc;

    std::vector<FlowDef> flowDefs = { fd };

    FlowResult flows = InstallGroupcastFlows(ueNodes, stack, flowDefs,
                                             simTime, harqEnabled, stack.stream);

    // ── 4. KPI hooks ───────────────────────────────────────────────────────
    const std::string dbBase = outputDir + simTag;
    KpiHooks hooks(dbBase, outPacketsCsv);

    double txAppDuration = 0.0;
    for (size_t f = 0; f < flows.defs.size(); ++f)
        txAppDuration = std::max(txAppDuration,
                                 flows.appStop[f] - flows.appStart[f]);

    hooks.v2xKpi.SetDbPath(dbBase);
    hooks.v2xKpi.SetTxAppDuration(txAppDuration);
    hooks.v2xKpi.SetRangeForV2xKpis(1000); // 1000-m gate (matches csi_groupcast_eval)

    ConnectPhyMacTraces(hooks);
    ConnectAppTraces(hooks, flows);

    // ── 5. Run ─────────────────────────────────────────────────────────────
    double simStop = 0.0;
    for (size_t f = 0; f < flows.defs.size(); ++f)
        simStop = std::max(simStop, flows.appStart[f] + simTime + 0.5);
    Simulator::Stop(Seconds(simStop));
    Simulator::Run();

    // ── 6. Flush + summary CSV ─────────────────────────────────────────────
    Flush(hooks);

    if (!outRunCsv.empty())
    {
        std::ofstream r(outRunCsv);
        if (r.is_open())
        {
            r << "runId,seed,trafficClass,baseScenario,mcs,bwPRBs,psfchPeriod,"
                 "maxNumTx,harq,simTime,warmup,db_path\n"
              << runId << ',' << seed << ',' << trafficClassStr << ','
              << baseScenarioStr << ',' << mcs << ',' << bwPRBs << ','
              << psfchPeriod << ',' << maxNumTx << ',' << (harqEnabled ? 1 : 0)
              << ',' << simTime << ',' << warmup << ',' << dbBase << ".db\n";
        }
    }

    Simulator::Destroy();
    NS_LOG_INFO("Done: " << runId);
    return 0;
}
