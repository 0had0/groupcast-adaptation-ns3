// traffic_class_raapc_eval.cc
//
// SINR-based RA-APC-Groupcast counterpart to traffic_class_adaptive_eval/.
//
// Identical setup to traffic_class_adaptive_eval but the adaptation signal is
// **per-RX SINR** (measured at the PHY layer of each receiver) rather than
// PSFCH NACK rate.  The controller runs the RA-APC-Groupcast algorithm:
//
//   Strategy A (raApcA): groupMcs = min_i m_feas[i]      — worst-case receiver
//   Strategy B (raApcB): groupMcs = P5({m_feas[i]})      — 5th-percentile
//
// then refines via ETP = δ(m) × F(m) within the feasible set.
//
// The key wiring difference vs traffic_class_adaptive_eval:
//   kpi_hooks::sinrSink is set to adaptCtrl->OnPsschRxSinr BEFORE
//   ConnectPhyMacTraces — the PSSCH-RX trace then feeds every received TB's
//   linear SINR (converted to dB) into the controller's sliding window.
//
// Because SINR is a PHY-layer measurement that arrives even when the TB
// decoder fails completely, this controller sees a signal even when NACKs
// are absent — the "quiet channel vs dead channel" problem is resolved.

#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
#include "common/mobility/mobility.h"
#include "common/stack/nr-sl-adaptive-mcs-controller.h"
#include "common/stack/nr-sl-adaptive-mcs-scheduler.h"
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
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("TrafficClassRaApcEval");

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

static std::vector<uint8_t>
BuildMcsLadder(const std::string& csv, uint32_t mcsMin, uint32_t mcsMax)
{
    std::vector<uint8_t> out;
    if (csv.empty())
    {
        if (mcsMax < mcsMin)
            throw std::runtime_error("--adaptMcsMax must be >= --adaptMcsMin");
        for (int m = static_cast<int>(mcsMax); m >= static_cast<int>(mcsMin); --m)
            out.push_back(static_cast<uint8_t>(m));
        return out;
    }
    std::stringstream ss(csv);
    std::string tok;
    while (std::getline(ss, tok, ','))
    {
        if (tok.empty()) continue;
        int v = std::stoi(tok);
        if (v < 0 || v > 28)
            throw std::runtime_error("--adaptMcsLadder values must be in [0,28]: " + tok);
        out.push_back(static_cast<uint8_t>(v));
    }
    if (out.empty())
        throw std::runtime_error("--adaptMcsLadder parsed to empty list");
    return out;
}

// ---------------------------------------------------------------------------
int
main(int argc, char* argv[])
{
    // ── Baseline CLI (identical to traffic_class_eval) ────────────────────
    std::string trafficClassStr = "periodic2";
    std::string mobilityTrace;
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
    uint32_t    rri         = 0;
    std::string runId       = "run0";
    std::string outputDir   = "./";
    std::string outRunCsv;
    std::string outPacketsCsv;
    std::string simTag;

    double      blockerL    = 4.5;
    double      blockerW    = 2.0;

    // ── RA-APC controller CLI ─────────────────────────────────────────────
    bool        adaptiveMcs    = true;
    std::string adaptMode      = "raApcA";  // raApcA | raApcB | nackRate
    uint32_t    adaptWinMs     = 500;
    uint32_t    adaptEvalMs    = 100;
    uint32_t    adaptInitMcs   = 14;
    uint32_t    adaptMcsMin    = 0;
    uint32_t    adaptMcsMax    = 28;
    std::string adaptMcsLadder = "";

    // NACK-rate params (kept for --adaptMode=nackRate fallback)
    double      adaptDownThr   = 0.15;
    double      adaptUpThr     = 0.08;
    uint32_t    adaptHyst      = 2;
    bool        adaptPerFlow   = false;   // SINR is per-RX node, not per-flow

    // RA-APC specific
    uint32_t    payloadBytes   = 1500;    // expected app payload (bytes) for rbLen LUT
    uint32_t    poolPrbs       = 100;     // total sidelink resource pool PRBs
    double      blerSlope      = 1.0;     // sigmoid steepness around 10%-BLER threshold

    CommandLine cmd(__FILE__);
    cmd.AddValue("trafficClass",   "Traffic class: periodic1..3 | aperiodic1..2 | onoff",
                                                                                trafficClassStr);
    cmd.AddValue("mobilityTrace",  "Pre-recorded waypoint CSV path (required)",  mobilityTrace);
    cmd.AddValue("baseScenario",   "3GPP scenario: V2V_Highway | V2V_Urban | "
                                   "UMi_StreetCanyon | UMa",                    baseScenarioStr);
    cmd.AddValue("shadowing",      "Enable shadowing in base channel model",     shadowing);
    cmd.AddValue("txNodeId",       "Node ID of the sole transmitter",            txNodeId);
    cmd.AddValue("numUEs",         "Number of UE nodes",                         numUEs);
    cmd.AddValue("simTime",        "Per-flow traffic duration (s)",              simTime);
    cmd.AddValue("warmup",         "Warm-up seconds discarded by V2xKpi",        warmup);
    cmd.AddValue("mcs",            "Initial MCS index (controller may move it)", mcs);
    cmd.AddValue("bwPRBs",         "Subchannel size W in PRBs",                  bwPRBs);
    cmd.AddValue("harq",           "Enable HARQ globally",                       harqEnabled);
    cmd.AddValue("psfchPeriod",    "PSFCH period in slots (1/2/4)",              psfchPeriod);
    cmd.AddValue("maxNumTx",       "Max PSSCH transmissions per TB (1..32)",     maxNumTx);
    cmd.AddValue("mu",             "Numerology (0=15kHz | 1=30kHz | 2=60kHz)",  mu);
    cmd.AddValue("seed",           "Random seed (rngRun)",                       seed);
    cmd.AddValue("rri",            "Resource Reservation Interval in ms",        rri);
    cmd.AddValue("runId",          "Unique run identifier",                      runId);
    cmd.AddValue("outputDir",      "Directory for KPI SQLite DB",                outputDir);
    cmd.AddValue("outRunCsv",      "One-line per-run summary CSV path",          outRunCsv);
    cmd.AddValue("outPacketsCsv",  "Per-packet CSV path",                        outPacketsCsv);
    cmd.AddValue("simTag",         "DB filename tag (default = runId)",          simTag);
    cmd.AddValue("blockerL",       "Blocker AABB length (m)",                    blockerL);
    cmd.AddValue("blockerW",       "Blocker AABB width (m)",                     blockerW);

    cmd.AddValue("adaptiveMcs",    "Enable adaptive MCS controller",             adaptiveMcs);
    cmd.AddValue("adaptMode",      "Algorithm: raApcA | raApcB | nackRate",      adaptMode);
    cmd.AddValue("adaptWinMs",     "Sliding-window length (ms)",                 adaptWinMs);
    cmd.AddValue("adaptEvalMs",    "Controller evaluation interval (ms)",        adaptEvalMs);
    cmd.AddValue("adaptInitMcs",   "Initial MCS for controller",                 adaptInitMcs);
    cmd.AddValue("adaptMcsMin",    "Bottom of MCS ladder (0..28)",               adaptMcsMin);
    cmd.AddValue("adaptMcsMax",    "Top of MCS ladder (0..28)",                  adaptMcsMax);
    cmd.AddValue("adaptMcsLadder", "Comma-separated MCS ladder (descending)",    adaptMcsLadder);
    cmd.AddValue("adaptDownThr",   "NACK rate threshold for step-down (nackRate mode)",
                                                                                adaptDownThr);
    cmd.AddValue("adaptUpThr",     "NACK rate threshold for step-up (nackRate mode)",
                                                                                adaptUpThr);
    cmd.AddValue("adaptHyst",      "Up-hysteresis count (nackRate mode)",        adaptHyst);
    cmd.AddValue("adaptPerFlow",   "Per-flow adaptation (nackRate mode)",        adaptPerFlow);
    cmd.AddValue("payloadBytes",   "Expected app payload in bytes (RA-APC rbLen LUT)",
                                                                                payloadBytes);
    cmd.AddValue("poolPrbs",       "Total sidelink pool PRBs (RA-APC δ calc)",   poolPrbs);
    cmd.AddValue("blerSlope",      "Sigmoid steepness 1/dB around 10%-BLER pt", blerSlope);
    cmd.Parse(argc, argv);

    // ── Validate ───────────────────────────────────────────────────────────
    if (mobilityTrace.empty())
    {
        std::cerr << "traffic_class_raapc_eval: --mobilityTrace is required\n";
        return 1;
    }
    if (maxNumTx < 1 || maxNumTx > 32)
    {
        std::cerr << "traffic_class_raapc_eval: --maxNumTx must be in [1,32]\n";
        return 1;
    }
    if (psfchPeriod != 0 && psfchPeriod != 1 && psfchPeriod != 2 && psfchPeriod != 4)
    {
        std::cerr << "traffic_class_raapc_eval: --psfchPeriod must be 0, 1, 2, or 4\n";
        return 1;
    }
    // RA-APC doesn't need PSFCH — only enforce if nackRate mode
    if (adaptiveMcs && adaptMode == "nackRate" && psfchPeriod == 0)
    {
        std::cerr << "traffic_class_raapc_eval: --adaptMode=nackRate requires --psfchPeriod > 0\n";
        return 1;
    }
    if (adaptMcsMin > 28 || adaptMcsMax > 28 || adaptMcsMin > adaptMcsMax)
    {
        std::cerr << "traffic_class_raapc_eval: invalid --adaptMcsMin/--adaptMcsMax\n";
        return 1;
    }
    if (adaptInitMcs > 28)
    {
        std::cerr << "traffic_class_raapc_eval: --adaptInitMcs must be in [0,28]\n";
        return 1;
    }
    if (simTag.empty()) simTag = runId;
    numUEs = std::max(numUEs, 2u);

    TrafficClass tc;
    BandwidthPartInfo::Scenario baseScenario;
    std::vector<uint8_t> mcsLadder;
    try
    {
        tc           = ParseTrafficClass(trafficClassStr);
        baseScenario = ParseScenario(baseScenarioStr);
        mcsLadder    = BuildMcsLadder(adaptMcsLadder, adaptMcsMin, adaptMcsMax);
    }
    catch (const std::exception& e)
    {
        std::cerr << "traffic_class_raapc_eval: " << e.what() << "\n";
        return 1;
    }

    if (txNodeId >= numUEs)
    {
        std::cerr << "traffic_class_raapc_eval: --txNodeId (" << txNodeId
                  << ") must be < --numUEs (" << numUEs << ")\n";
        return 1;
    }

    RngSeedManager::SetSeed(1);
    RngSeedManager::SetRun(seed);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    if (rri != 0)
    {
        bool validRri = (rri >= 10 && rri <= 100  && rri % 10 == 0) ||
                        (rri >= 150 && rri <= 1000 && rri % 50 == 0 && rri != 250);
        if (!validRri)
        {
            std::cerr << "traffic_class_raapc_eval: invalid --rri value\n";
            return 1;
        }
    }

    // ── 1. Mobility ────────────────────────────────────────────────────────
    MobConfig mobCfg;
    mobCfg.numUEs           = numUEs;
    mobCfg.seed             = seed;
    mobCfg.mobilityTraceCsv = mobilityTrace;
    MobResult mob = InstallMobility(mobCfg);
    NodeContainer& ueNodes = mob.ueNodes;

    // ── 2. NR/SL stack + geometric NLOSv ──────────────────────────────────
    GeomChannelCfg geomCfg;
    geomCfg.blockerLengthM = blockerL;
    geomCfg.blockerWidthM  = blockerW;
    geomCfg.blockerNodes   = ueNodes;

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

    // ── 3. Traffic flows ───────────────────────────────────────────────────
    Time rriTime = (rri > 0) ? MilliSeconds(static_cast<int64_t>(rri))
                              : DefaultRriForTrafficClass(tc);

    FlowDef fd;
    fd.label        = "tx_only";
    fd.port         = 8001;
    fd.priority     = 3;
    fd.rri          = rriTime;
    fd.dynamic      = (rriTime == Time(0));
    fd.pdb          = (rriTime > Time(0)) ? rriTime : MilliSeconds(100);
    fd.rateKbps     = 100.0;
    fd.enabled      = true;
    fd.harqOverride = -1;
    fd.txNodes      = { txNodeId };
    fd.startTime    = Time(0);
    fd.stopTime     = Time(0);
    fd.trafficClass = tc;

    FlowResult flows = InstallGroupcastFlows(ueNodes, stack, {fd},
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
    hooks.v2xKpi.SetRangeForV2xKpis(1000);

    // ── 4b. Adaptive MCS controller ────────────────────────────────────────
    Ptr<NrSlAdaptiveMcsController> adaptCtrl;
    if (adaptiveMcs)
    {
        Ptr<Node> txNode = ueNodes.Get(txNodeId);
        auto txDev = txNode->GetDevice(0)->GetObject<NrUeNetDevice>();
        NS_ABORT_MSG_IF(!txDev,
                        "RaApc: node " << txNodeId << " device 0 is not NrUeNetDevice");
        auto nrSlMac = txDev->GetMac(0)->GetObject<NrSlUeMac>();
        NS_ABORT_MSG_IF(!nrSlMac, "RaApc: could not cast MAC to NrSlUeMac");
        PointerValue schedPv;
        nrSlMac->GetAttribute("NrSlUeMacScheduler", schedPv);
        auto adaptSched = schedPv.Get<NrSlUeMacScheduler>()
                                ->GetObject<NrSlAdaptiveMcsScheduler>();
        NS_ABORT_MSG_IF(!adaptSched,
                        "RaApc: scheduler is not NrSlAdaptiveMcsScheduler");

        adaptCtrl = CreateObject<NrSlAdaptiveMcsController>();
        adaptCtrl->SetScheduler(adaptSched);
        adaptCtrl->SetInitialMcs(static_cast<uint8_t>(adaptInitMcs));
        adaptCtrl->SetWindowMs(adaptWinMs);
        adaptCtrl->SetEvalIntervalMs(adaptEvalMs);
        adaptCtrl->SetMcsCandidates(mcsLadder);

        // RA-APC mode selection
        using Mode = NrSlAdaptiveMcsController::Mode;
        if      (adaptMode == "raApcA")   adaptCtrl->SetMode(Mode::RaApcWorstCase);
        else if (adaptMode == "raApcB")   adaptCtrl->SetMode(Mode::RaApcP5);
        else if (adaptMode == "nackRate") adaptCtrl->SetMode(Mode::NackRate);
        else
        {
            std::cerr << "traffic_class_raapc_eval: invalid --adaptMode '"
                      << adaptMode << "' (use raApcA|raApcB|nackRate)\n";
            return 1;
        }

        // RA-APC geometry/payload parameters
        adaptCtrl->SetPayloadBytes(payloadBytes);
        adaptCtrl->SetPoolPrbs(poolPrbs);
        adaptCtrl->SetBwPrbs(bwPRBs);
        adaptCtrl->SetBlerSlope(blerSlope);

        // NACK-rate params (used only if adaptMode=nackRate)
        adaptCtrl->SetDownThreshold(adaptDownThr);
        adaptCtrl->SetUpThreshold(adaptUpThr);
        adaptCtrl->SetUpHysteresis(adaptHyst);
        adaptCtrl->SetPerFlowAdaptation(adaptPerFlow);

        // Per-flow HARQ→LCID mapping (only needed for nackRate+perFlow)
        if (adaptPerFlow)
        {
            adaptSched->TraceConnectWithoutContext(
                "HarqAllocation",
                MakeCallback(&NrSlAdaptiveMcsController::AddHarqLcMapping, adaptCtrl));
        }

        // SINR sink: feed every received TB's SINR (dB) into the controller.
        // kpi_hooks::OnSlPsschRx already converts p.m_sinr (linear) to dB and
        // calls (*sinrSink)(rnti, sinrDb) — we just hook in here.
        // Must be set before ConnectPhyMacTraces so the callback pointer is live.
        hooks.sinrSink = [adaptCtrl](uint16_t rnti, double sinrDb)
        {
            adaptCtrl->OnPsschRxSinr(rnti, sinrDb);
        };

        // PSFCH HARQ feedback (still useful for HARQ retransmission decisions
        // even in RA-APC mode; also enables nackRate fallback).
        Config::Connect(
            "/NodeList/*/DeviceList/0/$ns3::NrUeNetDevice"
            "/ComponentCarrierMapUe/*/NrUeMac/$ns3::NrSlUeMac"
            "/NrSlUeMacHarq/RxHarqFeedback",
            MakeCallback(&NrSlAdaptiveMcsController::OnHarqFeedback, adaptCtrl));

        // Adaptation-decision log → adaptLog SQLite table
        adaptCtrl->SetAdaptLogCb([&hooks](double timeMs, double metric,
                                          uint8_t prevMcs, uint8_t newMcs,
                                          const std::string& decision,
                                          uint32_t ackCount, uint32_t nackCount,
                                          double windowStartMs, double windowEndMs)
        {
            hooks.WriteAdaptLog(timeMs, metric, prevMcs, newMcs, decision,
                                ackCount, nackCount, windowStartMs, windowEndMs);
        });

        adaptCtrl->Start();
    }

    // Connect traces AFTER sinrSink is set (the callback checks it at runtime)
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
                 "maxNumTx,harq,simTime,warmup,db_path,scheme,adapt_mode,"
                 "adapt_win_ms,adapt_eval_ms,adapt_init_mcs,adapt_mcs_min,"
                 "adapt_mcs_max,payload_bytes,pool_prbs,bler_slope\n"
              << runId << ',' << seed << ',' << trafficClassStr << ','
              << baseScenarioStr << ',' << mcs << ',' << bwPRBs << ','
              << psfchPeriod << ',' << maxNumTx << ',' << (harqEnabled ? 1 : 0)
              << ',' << simTime << ',' << warmup << ',' << dbBase << ".db,"
              << (adaptiveMcs ? "raapc" : "fixed") << ','
              << adaptMode << ',' << adaptWinMs << ',' << adaptEvalMs << ','
              << adaptInitMcs << ',' << adaptMcsMin << ',' << adaptMcsMax << ','
              << payloadBytes << ',' << poolPrbs << ',' << blerSlope << "\n";
        }
    }

    Simulator::Destroy();
    NS_LOG_INFO("Done: " << runId);
    return 0;
}
