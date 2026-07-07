// csi_groupcast_eval.cc
//
// SUMO-driven NR-V2X groupcast evaluator with geometry-aware NLOSv channel.
//
// 7-step orchestration (mirrors scratch_overlay/groupcast_eval):
//   1. CLI parsing
//   2. InstallMobility()          → SUMO/TraCI, returns ueNodes + traci
//   3. BuildSlStack()             → NR/SL stack + geometric-NLOSv channel
//   4. InstallGroupcastFlows()    → multi-flow V2X bearer + OnOff/PacketSink
//   5. KpiHooks + trace connect   → SQLite DB + optional CSV
//   6. Simulator::Run()
//   7. Flush() + run-summary CSV

#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
#include "common/mobility/mobility.h"
#include "common/stack/nr-sl-adaptive-mcs-controller.h"
#include "common/stack/nr-sl-adaptive-mcs-scheduler.h"
#include "common/stack/nr_sl_stack.h"
#include "common/kpi/v2x-kpi.h"
#include <sqlite3.h>

#include "ns3/core-module.h"
#include "ns3/log.h"
#include "ns3/nr-sl-ue-mac.h"
#include "ns3/nr-ue-net-device.h"

#include <algorithm>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("CsiGroupcastEval");

// ---------------------------------------------------------------------------
// Helper: split a comma-separated string into a vector of trimmed tokens.
// ---------------------------------------------------------------------------
static std::vector<std::string>
SplitCsv(const std::string& s)
{
    std::vector<std::string> out;
    std::istringstream ss(s);
    std::string tok;
    while (std::getline(ss, tok, ','))
    {
        // trim whitespace
        auto b = tok.find_first_not_of(" \t");
        auto e = tok.find_last_not_of(" \t");
        if (b != std::string::npos)
            out.push_back(tok.substr(b, e - b + 1));
    }
    return out;
}

// ---------------------------------------------------------------------------
// Helper: parse --baseScenario string → BandwidthPartInfo::Scenario.
// ---------------------------------------------------------------------------
static BandwidthPartInfo::Scenario
ParseScenario(const std::string& s)
{
    if (s == "V2V_Highway")     return BandwidthPartInfo::V2V_Highway;
    if (s == "V2V_Urban")       return BandwidthPartInfo::V2V_Urban;
    if (s == "UMi_StreetCanyon") return BandwidthPartInfo::UMi_StreetCanyon;
    if (s == "UMa")              return BandwidthPartInfo::UMa;
    throw std::runtime_error("Unknown --baseScenario: " + s +
                             " (accepted: V2V_Highway | V2V_Urban | UMi_StreetCanyon | UMa)");
}

// ---------------------------------------------------------------------------
int
main(int argc, char* argv[])
{
    // -- CLI -----------------------------------------------------------------
    std::string runId        = "run0";
    uint32_t    seed         = 1;
    uint32_t    numUEs       = 8;
    uint32_t    mu           = 1;
    uint32_t    bwPRBs       = 50;
    uint32_t    mcs          = 14;
    bool        adaptiveMcs  = false;
    bool        perFlowAdapt = false;  // Enable per-flow MCS adaptation (default: per-destination)
    std::string adaptMode    = "nackRate";  // nackRate | raApcA | raApcB
    std::string etpVariant   = "avg";       // avg | strict
    double      adaptDownThr = 0.15;   // NACK rate → step down
    double      adaptUpThr   = 0.08;   // NACK rate → consider step up (mean-RX floor in NLOSv)
    uint32_t    adaptWinMs   = 500;    // sliding window (ms)
    uint32_t    adaptEvalMs  = 500;    // evaluation period (ms)
    uint32_t    raApcPayloadBytes = 0; // 0 = auto from flow spec
    uint32_t    raApcPoolPrbs     = 0; // 0 = auto from bwPRBs (assumes pool = 100 PRBs)
    double      raApcBlerSlope    = 1.0;
    double      simTime      = 10.0;
    double      warmup       = 1.0;
    bool        harqEnabled  = true;
    uint32_t    maxNumTx     = 5;
    uint32_t    psfchPeriod  = 0;
    std::string flowsSpec;

    // SUMO (default) / mobility-trace (optional)
    std::string sumoCfg;
    std::string sumoBinary      = "";
    double      sumoSync        = 0.1;
    std::string mobilityTrace;  // path to CSV from export_fcd_trace.py; when set SUMO is skipped

    // Geometric NLOSv
    std::string blockerIdsStr;  // comma-separated SUMO IDs
    double      blockerL     = 5.0;
    double      blockerW     = 2.5;
    std::string baseScenarioStr = "V2V_Urban";
    bool        shadowing    = true;

    // Output
    std::string outputDir    = "./";
    std::string outRunCsv;
    std::string outPacketsCsv;
    std::string simTag;

    // PSFCH per-opportunity ACK/NACK/SILENCE KPI (additive, default off).
    // See scratch_overlay/common/psfch_kpi/ for the module. The KPI shim
    // installs inside BuildSlStack when sp.psfchKpiPath is non-empty; we
    // leave it empty by default to preserve prior behavior.
    bool        psfchKpi           = false;
    bool        psfchKpiSummaryOnly = false;
    std::string psfchKpiPath;

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId",         "Unique run identifier",                          runId);
    cmd.AddValue("seed",          "Random seed",                                    seed);
    cmd.AddValue("numUEs",        "Number of UE nodes",                             numUEs);
    cmd.AddValue("mu",            "Numerology (0=15kHz | 1=30kHz | 2=60kHz)",       mu);
    cmd.AddValue("bwPRBs",        "Subchannel size W in PRBs",                      bwPRBs);
    cmd.AddValue("mcs",           "Fixed MCS index (initial MCS when adaptiveMcs=true)", mcs);
    cmd.AddValue("adaptiveMcs",   "Enable per-RX NACK-driven MCS adaptation",       adaptiveMcs);
    cmd.AddValue("perFlowAdapt",  "Enable per-flow MCS adaptation (default: per-destination)", perFlowAdapt);
    cmd.AddValue("adaptMode",     "Adaptation algorithm: nackRate | raApcA | raApcB", adaptMode);
    cmd.AddValue("etpVariant",    "Group-ETP objective: avg | strict",              etpVariant);
    cmd.AddValue("raApcPayloadBytes", "RA-APC payload size (0 = auto from flows)",  raApcPayloadBytes);
    cmd.AddValue("raApcPoolPrbs",     "RA-APC pool size in PRBs (0 = 10 * numUEs)", raApcPoolPrbs);
    cmd.AddValue("raApcBlerSlope",    "RA-APC BLER sigmoid slope (1/dB)",           raApcBlerSlope);
    cmd.AddValue("adaptDownThr",  "Adaptive MCS: worst-RX NACK rate → step down",   adaptDownThr);
    cmd.AddValue("adaptUpThr",    "Adaptive MCS: mean-RX NACK rate → step up",      adaptUpThr);
    cmd.AddValue("adaptWinMs",    "Adaptive MCS: sliding-window length (ms)",        adaptWinMs);
    cmd.AddValue("adaptEvalMs",   "Adaptive MCS: evaluation period (ms)",            adaptEvalMs);
    cmd.AddValue("simTime",       "Traffic duration per flow (s)",                  simTime);
    cmd.AddValue("warmup",        "Warm-up seconds discarded by V2xKpi",            warmup);
    cmd.AddValue("harq",          "Enable HARQ globally",                           harqEnabled);
    cmd.AddValue("maxNumTx",      "Max PSSCH transmissions per TB (1..32)",         maxNumTx);
    cmd.AddValue("psfchPeriod",   "PSFCH period in slots (0=off, 1/2/4=on)",        psfchPeriod);
    cmd.AddValue("flows",         "Flow spec (comma-separated flows, each ;-sep)",  flowsSpec);
    cmd.AddValue("sumoCfg",       "Path to SUMO .sumocfg (required unless --mobilityTrace is set)", sumoCfg);
    cmd.AddValue("sumoBinary",    "SUMO binary path",                               sumoBinary);
    cmd.AddValue("sumoSync",      "TraCI sync interval (s)",                        sumoSync);
    cmd.AddValue("mobilityTrace", "Pre-recorded waypoint CSV (output of export_fcd_trace.py); "
                                  "when set, SUMO is not launched and the CSV drives mobility",
                                  mobilityTrace);
    cmd.AddValue("blockerIds",    "Comma-separated SUMO vehicle IDs for NLOSv",     blockerIdsStr);
    cmd.AddValue("blockerL",      "Blocker vehicle length (m)",                     blockerL);
    cmd.AddValue("blockerW",      "Blocker vehicle width (m)",                      blockerW);
    cmd.AddValue("baseScenario",  "3GPP base scenario: V2V_Highway | V2V_Urban | "
                                  "UMi_StreetCanyon | UMa",                         baseScenarioStr);
    cmd.AddValue("shadowing",     "Enable shadowing in base channel model",         shadowing);
    cmd.AddValue("outputDir",     "Directory for KPI SQLite DB",                    outputDir);
    cmd.AddValue("outRunCsv",     "One-line per-run summary CSV path",              outRunCsv);
    cmd.AddValue("outPacketsCsv", "Per-packet CSV path",                            outPacketsCsv);
    cmd.AddValue("simTag",        "DB filename tag (default = runId)",              simTag);
    cmd.AddValue("psfchKpi",          "Record per-PSFCH-opportunity ACK/NACK/SILENCE [false]", psfchKpi);
    cmd.AddValue("psfchKpiSummaryOnly","Emit only the summary CSV (no per-event rows) [false]", psfchKpiSummaryOnly);
    cmd.AddValue("psfchKpiPath",      "Override PSFCH KPI CSV path (default = outputDir/simTag_psfch_opps.csv)",
                  psfchKpiPath);
    cmd.Parse(argc, argv);

    // Validate
    if (maxNumTx < 1 || maxNumTx > 32)
    {
        std::cerr << "csi_groupcast_eval: --maxNumTx must be in [1,32]\n";
        return 1;
    }
    if (psfchPeriod != 0 && psfchPeriod != 1 &&
        psfchPeriod != 2 && psfchPeriod != 4)
    {
        std::cerr << "csi_groupcast_eval: --psfchPeriod must be 0, 1, 2, or 4\n";
        return 1;
    }
    if (simTag.empty())
        simTag = runId;
    numUEs = std::max(numUEs, 2u);

    BandwidthPartInfo::Scenario baseScenario;
    try { baseScenario = ParseScenario(baseScenarioStr); }
    catch (const std::exception& e)
    {
        std::cerr << "csi_groupcast_eval: " << e.what() << "\n";
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
        std::cerr << "csi_groupcast_eval: --flows parse error: " << e.what() << "\n";
        return 1;
    }

    std::vector<std::string> blockerIds = SplitCsv(blockerIdsStr);

    RngSeedManager::SetSeed(1);
    RngSeedManager::SetRun(seed);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    // -----------------------------------------------------------------------
    // 2. Mobility
    // -----------------------------------------------------------------------
    MobConfig mobCfg;
    mobCfg.numUEs             = numUEs;
    mobCfg.seed               = seed;
    mobCfg.sumoCfg            = sumoCfg;
    mobCfg.sumoBinary         = sumoBinary;
    mobCfg.sumoSyncInterval   = sumoSync;
    mobCfg.mobilityTraceCsv   = mobilityTrace;

    MobResult mob = InstallMobility(mobCfg);
    NodeContainer& ueNodes = mob.ueNodes;

    // -----------------------------------------------------------------------
    // 3. NR/SL stack with geometric-NLOSv channel
    // -----------------------------------------------------------------------
    GeomChannelCfg geomCfg;
    geomCfg.blockerLengthM  = blockerL;
    geomCfg.blockerWidthM   = blockerW;
    if (mobilityTrace.empty())
    {
        // SUMO mode: look up blocker positions via TraCI node map.
        geomCfg.blockerSumoIds = blockerIds;
        geomCfg.traci          = mob.traci;
    }
    else
    {
        // Trace mode: all UE nodes serve as blockers (positions from WaypointMobilityModel).
        geomCfg.blockerNodes = ueNodes;
    }

    SlStackParams stackParams;
    stackParams.mu               = mu;
    stackParams.bwPRBs           = bwPRBs;
    stackParams.mcs              = mcs;
    stackParams.adaptiveMcs      = adaptiveMcs;  // CRITICAL: SlStackParams defaults
                                                 // adaptiveMcs{true}; without this
                                                 // assignment every fixed-MCS run
                                                 // silently installed the adaptive
                                                 // scheduler and transmitted at its
                                                 // m_adaptiveMcs=14 default, ignoring --mcs.
    stackParams.seed             = seed;
    stackParams.maxNumTx         = static_cast<uint8_t>(maxNumTx);
    stackParams.psfchPeriod      = static_cast<uint16_t>(psfchPeriod);
    stackParams.shadowingEnabled = shadowing;
    stackParams.scenario         = baseScenario;

    // PSFCH per-opportunity KPI — only enabled when --psfchKpi is set.
    // Empty psfchKpiPath disables the install inside BuildSlStack.
    stackParams.recordPsfchKpi    = psfchKpi;
    stackParams.psfchKpiSummaryOnly = psfchKpiSummaryOnly;
    if (psfchKpi && psfchKpiPath.empty())
    {
        stackParams.psfchKpiPath = outputDir + "/" + simTag + "_psfch_opps.csv";
    }
    else
    {
        stackParams.psfchKpiPath = psfchKpiPath;
    }

    // Per-destination fixed-MCS overrides: any flow carrying an explicit mcs=
    // pins its dstL2Id to that MCS (dual-MCS redundant groupcast). Flows without
    // mcs= (fd.mcs < 0) keep the global --mcs default for their destination.
    for (const auto& fd : flowDefs)
    {
        if (fd.mcs >= 0)
        {
            stackParams.dstMcsOverride[fd.dstL2Id] =
                static_cast<uint8_t>(fd.mcs);
        }
    }

    SlStackCtx stack = BuildSlStack(stackParams, geomCfg, ueNodes);

    // -----------------------------------------------------------------------
    // 4. Groupcast flows
    // -----------------------------------------------------------------------
    FlowResult flows = InstallGroupcastFlows(ueNodes, stack, flowDefs,
                                             simTime, harqEnabled, stack.stream);

    // -----------------------------------------------------------------------
    // 5. KPI hooks
    // -----------------------------------------------------------------------
    const std::string dbBase = outputDir + simTag;
    KpiHooks hooks(dbBase, outPacketsCsv);

    double txAppDuration = 0.0;
    for (size_t f = 0; f < flows.defs.size(); ++f)
        txAppDuration = std::max(txAppDuration,
                                 flows.appStop[f] - flows.appStart[f]);

    hooks.v2xKpi.SetDbPath(dbBase);
    hooks.v2xKpi.SetTxAppDuration(txAppDuration);
    hooks.v2xKpi.SetRangeForV2xKpis(1000); // 1000-metre range gate

    ConnectPhyMacTraces(hooks);
    ConnectAppTraces(hooks, flows);

    // -----------------------------------------------------------------------
    // 5b. Adaptive MCS controller (optional, requires psfchPeriod > 0)
    // -----------------------------------------------------------------------
    Ptr<NrSlAdaptiveMcsController> adaptCtrl;
    if (adaptiveMcs)
    {
        if (psfchPeriod == 0)
        {
            std::cerr << "csi_groupcast_eval: --adaptiveMcs requires --psfchPeriod > 0\n";
            return 1;
        }

        // Identify TX node index (first flow with explicit txNodes, else node 0).
        uint32_t txIdx = 0;
        for (auto& fd : flowDefs)
            if (!fd.txNodes.empty()) { txIdx = fd.txNodes[0]; break; }

        Ptr<Node> txNode = ueNodes.Get(txIdx);

        // Reach scheduler on TX node: MAC → NrSlUeMacScheduler attribute
        // → cast to NrSlAdaptiveMcsScheduler (always installed by BuildSlStack).
        auto txDev = txNode->GetDevice(0)->GetObject<NrUeNetDevice>();
        NS_ABORT_MSG_IF(!txDev, "AdaptiveMcs: node " << txIdx << " device 0 is not NrUeNetDevice");
        auto nrSlMac = txDev->GetMac(0)->GetObject<NrSlUeMac>();
        NS_ABORT_MSG_IF(!nrSlMac, "AdaptiveMcs: could not cast MAC to NrSlUeMac");
        PointerValue schedPv;
        nrSlMac->GetAttribute("NrSlUeMacScheduler", schedPv);
        auto adaptSched = schedPv.Get<NrSlUeMacScheduler>()
                              ->GetObject<NrSlAdaptiveMcsScheduler>();
        NS_ABORT_MSG_IF(!adaptSched,
                        "AdaptiveMcs: scheduler is not NrSlAdaptiveMcsScheduler — "
                        "check BuildSlStack SetNrSlSchedulerTypeId");

        adaptCtrl = CreateObject<NrSlAdaptiveMcsController>();
        adaptCtrl->SetScheduler(adaptSched);
        adaptCtrl->SetInitialMcs(static_cast<uint8_t>(mcs));
        adaptCtrl->SetWindowMs(adaptWinMs);
        adaptCtrl->SetEvalIntervalMs(adaptEvalMs);
        adaptCtrl->SetDownThreshold(adaptDownThr);
        adaptCtrl->SetUpThreshold(adaptUpThr);
        adaptCtrl->SetUpHysteresis(5);
        adaptCtrl->SetPerFlowAdaptation(perFlowAdapt);

        // Connect to scheduler's HarqAllocation trace to track LC IDs
        // This enables per-flow adaptation by linking HARQ feedback to specific flows
        if (perFlowAdapt)
        {
            // Use a static callback wrapper to capture the controller pointer
            adaptSched->TraceConnectWithoutContext(
                "HarqAllocation",
                MakeCallback(&NrSlAdaptiveMcsController::AddHarqLcMapping, adaptCtrl));
        }

        // Select adaptation mode and ladder.
        using Mode = NrSlAdaptiveMcsController::Mode;
        using EtpV = NrSlAdaptiveMcsController::EtpVariant;
        Mode mode;
        if      (adaptMode == "nackRate") mode = Mode::NackRate;
        else if (adaptMode == "raApcA")   mode = Mode::RaApcWorstCase;
        else if (adaptMode == "raApcB")   mode = Mode::RaApcP5;
        else {
            std::cerr << "csi_groupcast_eval: invalid --adaptMode '" << adaptMode
                      << "' (use nackRate|raApcA|raApcB)\n";
            return 1;
        }
        adaptCtrl->SetMode(mode);

        if      (etpVariant == "avg")    adaptCtrl->SetEtpVariant(EtpV::Average);
        else if (etpVariant == "strict") adaptCtrl->SetEtpVariant(EtpV::Strict);
        else {
            std::cerr << "csi_groupcast_eval: invalid --etpVariant '" << etpVariant
                      << "' (use avg|strict)\n";
            return 1;
        }

        if (mode == Mode::NackRate)
        {
            // Legacy ladder used in the static-vs-adaptive comparison.
            adaptCtrl->SetMcsCandidates({28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 7, 6, 5, 4, 3, 2, 1, 0});
        }
        else
        {
            // RA-APC explores the full ladder; the ETP search clamps by group demand.
            adaptCtrl->SetMcsCandidates({28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 7, 6, 5, 4, 3, 2, 1, 0});

            // Auto-derive payload + pool size from existing config if not given.
            // The "+50 byte" margin covers PDCP/RLC/MAC headers (~20 B) and the
            // 5-byte SCI-2A overhead the scheduler adds in its sizing loop. This
            // matches the empirically-observed rbLen=20 PRBs at MCS=14 for a
            // 250-byte payload.
            uint32_t payload = raApcPayloadBytes;
            if (payload == 0 && !flowDefs.empty())
            {
                const auto& f = flowDefs[0];
                payload = static_cast<uint32_t>(
                    f.rateKbps * 1000.0 * f.rri.GetSeconds() / 8.0) + 50;
            }
            if (payload == 0) payload = 300;
            uint32_t pool = raApcPoolPrbs;
            if (pool == 0) pool = numUEs * bwPRBs;
            adaptCtrl->SetPayloadBytes(payload);
            adaptCtrl->SetPoolPrbs(pool);
            adaptCtrl->SetBwPrbs(bwPRBs);
            adaptCtrl->SetBlerSlope(raApcBlerSlope);

            // Wire the per-RX SINR sink (KpiHooks → controller).
            // OnSlPsschRx was bound with the *address* of hooks.sinrSink, so
            // setting it now (post-ConnectPhyMacTraces) is correct.
            auto ctrl = adaptCtrl;  // weakly-captured copy of the Ptr
            hooks.sinrSink = [ctrl](uint16_t rnti, double sinrDb) {
                ctrl->OnPsschRxSinr(rnti, sinrDb);
            };
        }

        // Connect to the EXISTING NrSlUeMacHarq::RxHarqFeedback trace.
        // Wildcard path: only the TX UE fires (guarded by m_txRnti==GetRnti()
        // inside DoReceivePsfch), so no extra filtering is needed.
        Config::Connect(
            "/NodeList/*/DeviceList/0/$ns3::NrUeNetDevice"
            "/ComponentCarrierMapUe/*/NrUeMac/$ns3::NrSlUeMac"
            "/NrSlUeMacHarq/RxHarqFeedback",
            MakeCallback(&NrSlAdaptiveMcsController::OnHarqFeedback, adaptCtrl));
        // Set up adaptation logging callback to record each evaluation
        adaptCtrl->SetAdaptLogCb([&hooks](double timeMs, double metric, uint8_t prevMcs, uint8_t newMcs,
                                           const std::string& decision,
                                           uint32_t ackCount, uint32_t nackCount,
                                           double windowStartMs, double windowEndMs)
        {
            hooks.WriteAdaptLog(timeMs, metric, prevMcs, newMcs, decision,
                               ackCount, nackCount, windowStartMs, windowEndMs);
        });
        adaptCtrl->Start();
    }

    // -----------------------------------------------------------------------
    // 6. Run
    // -----------------------------------------------------------------------
    double simStop = 0.0;
    for (size_t f = 0; f < flows.defs.size(); ++f)
        simStop = std::max(simStop, flows.appStart[f] + simTime + 0.5);
    Simulator::Stop(Seconds(simStop));
    Simulator::Run();

    // -----------------------------------------------------------------------
    // 7. Flush + run-summary CSV
    // -----------------------------------------------------------------------
    Flush(hooks);

    if (!outRunCsv.empty())
    {
        std::ofstream r(outRunCsv);
        if (r.is_open())
        {
            r << "runId,seed,numUEs,mu,bwPRBs,mcs,simTime,warmup,"
                 "harq,maxNumTx,psfchPeriod,baseScenario,blockerCount,kpiDb\n"
              << runId << ',' << seed << ',' << numUEs << ',' << mu << ','
              << bwPRBs << ',' << mcs << ',' << simTime << ',' << warmup << ','
              << (harqEnabled ? 1 : 0) << ',' << maxNumTx << ',' << psfchPeriod << ','
              << baseScenarioStr << ',' << blockerIds.size() << ','
              << dbBase << ".db\n";
        }
    }

    Simulator::Destroy();
    NS_LOG_INFO("Done: " << runId);
    return 0;
}
