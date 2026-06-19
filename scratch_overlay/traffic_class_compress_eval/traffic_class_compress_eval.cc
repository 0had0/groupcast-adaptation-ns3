// traffic_class_compress_eval.cc
//
// Same setup as traffic_class_eval.cc (fixed MCS=14 baseline) but with an
// optional adaptive application-layer compression layer that intercepts each
// TX packet at the CustomTrafficApplication level:
//   - synthesises deterministic semi-compressible bytes for the class
//   - runs zstd/lz4 according to an adaptive decision tree
//   - attaches a CompressionTag carrying {codec, origSize, compSize, encodeUs}
//
// The RX side (in kpi_hooks::OnUePacket) reads the tag, decompresses for
// timing, and accounts pktTxRx using the original SDU size so PRR/PIR/tput
// remain application-layer KPIs.
//
// Builds on the shared csi_groupcast_eval support stack + the per-flow
// CompressionController in this directory.

#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
#include "common/mobility/mobility.h"
#include "common/stack/nr_sl_stack.h"
#include "common/kpi/v2x-kpi.h"
#include "common/traffic/custom-traffic-application.h"
#include "compression_controller.h"
#include "compression_tag.h"

#include "ns3/core-module.h"
#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/node-list.h"

#include <algorithm>
#include <cctype>
#include <exception>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("TrafficClassCompressEval");

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

// MCS table 2 (256QAM-capable) spectral efficiency (bits/RE) per 3GPP
// TS 38.214 Table 5.1.3.1-2. Used by the TbCap estimator only — the
// controller's decision is qualitative (N=1 vs N≥2), so an approximation is
// sufficient.
static constexpr double kMcsEff[29] = {
    0.2344, 0.3066, 0.3770, 0.4902, 0.6016, 0.7402, 0.8770, 1.0273,
    1.1758, 1.3262, 1.3281, 1.4766, 1.6953, 1.9141, 2.1602, 2.4063,
    2.5703, 2.7305, 3.0293, 3.3223, 3.6094, 3.9023, 4.2129, 4.5234,
    4.8164, 5.1152, 5.3320, 5.5547, 5.8896
};

// Approximate TB capacity in bytes for a configuration of (mcs, bwPRBs,
// totalSubChannels, symbolsPerSlot). 15% DMRS overhead assumed.
static uint32_t
EstimateTbBytes(uint32_t mcs, uint32_t bwPRBs, uint32_t totalSubCh)
{
    constexpr uint32_t syms = 9;     // PSSCH symbols per slot (with PSFCH worst-case)
    constexpr double useFrac = 0.85; // DMRS overhead
    const double eff = (mcs < 29) ? kMcsEff[mcs] : kMcsEff[28];
    const double bits = static_cast<double>(totalSubCh) * bwPRBs * 12.0 * syms * useFrac * eff;
    return static_cast<uint32_t>(bits / 8.0);
}

// Default per-class PDB (ms). Matches 3GPP TS 22.186 V2X service requirements
// for the relevant service categories.
static uint32_t
DefaultPdbMsForClass(const std::string& tc)
{
    if (tc == "periodic1")  return 100;
    if (tc == "periodic2")  return 100;
    if (tc == "periodic3")  return 500;
    if (tc == "aperiodic1") return 100;
    if (tc == "aperiodic2") return 500;
    return 100;
}

// Compute max(distance(tx, rx_i)) at simulator-now for adaptive distance
// signalling. RX positions are pulled from the mobility model of every UE
// except the txNodeId.
static double
MaxRxDistanceFromTx(NodeContainer& ueNodes, uint32_t txNodeId)
{
    if (ueNodes.GetN() < 2) return 0.0;
    Ptr<MobilityModel> tx = ueNodes.Get(txNodeId)->GetObject<MobilityModel>();
    if (!tx) return 0.0;
    const Vector txPos = tx->GetPosition();
    double maxD = 0.0;
    for (uint32_t i = 0; i < ueNodes.GetN(); ++i)
    {
        if (i == txNodeId) continue;
        Ptr<MobilityModel> rx = ueNodes.Get(i)->GetObject<MobilityModel>();
        if (!rx) continue;
        const Vector p = rx->GetPosition();
        const double dx = p.x - txPos.x;
        const double dy = p.y - txPos.y;
        const double dz = p.z - txPos.z;
        const double d = std::sqrt(dx * dx + dy * dy + dz * dz);
        if (d > maxD) maxD = d;
    }
    return maxD;
}

// ---------------------------------------------------------------------------
int
main(int argc, char* argv[])
{
    // ── CLI (inherits all flags from traffic_class_eval, plus --comp*) ─────
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

    // --- new compression-specific flags --------------------------------
    std::string compMode             = "adaptive";
    double      compNackHighThr      = 0.10;
    double      compDistFarThr       = 80.0;
    double      compEntropyHighThr   = 0.92;
    double      compDeadlineSlackMs  = 2.0;
    uint32_t    compPdbMs            = 0; // 0 → per-class default

    CommandLine cmd(__FILE__);
    cmd.AddValue("trafficClass",  "periodic1..3 | aperiodic1..2 | onoff",      trafficClassStr);
    cmd.AddValue("mobilityTrace", "Pre-recorded waypoint CSV path (required)", mobilityTrace);
    cmd.AddValue("baseScenario",  "V2V_Highway | V2V_Urban | UMi_StreetCanyon | UMa",
                                                                                baseScenarioStr);
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
    cmd.AddValue("mu",            "Numerology (0/1/2)",                         mu);
    cmd.AddValue("seed",          "Random seed (rngRun)",                       seed);
    cmd.AddValue("rri",           "RRI override (ms); 0 = class default",       rri);
    cmd.AddValue("runId",         "Unique run identifier",                      runId);
    cmd.AddValue("outputDir",     "Directory for KPI SQLite DB",                outputDir);
    cmd.AddValue("outRunCsv",     "One-line per-run summary CSV path",          outRunCsv);
    cmd.AddValue("outPacketsCsv", "Per-packet CSV path",                        outPacketsCsv);
    cmd.AddValue("simTag",        "DB filename tag (default = runId)",          simTag);
    cmd.AddValue("blockerL",      "Blocker AABB length (m)",                    blockerL);
    cmd.AddValue("blockerW",      "Blocker AABB width (m)",                     blockerW);

    cmd.AddValue("compMode",              "Compression mode: none | fixed_lz4 | "
                                          "fixed_zstd1 | fixed_zstd3 | adaptive",
                                                                                compMode);
    cmd.AddValue("compNackHighThr",       "Adaptive: escalate codec above this NACK rate",
                                                                                compNackHighThr);
    cmd.AddValue("compDistFarThr",        "Adaptive: escalate codec above this max-RX distance (m)",
                                                                                compDistFarThr);
    cmd.AddValue("compEntropyHighThr",    "Adaptive: skip codec if entropy estimate above this",
                                                                                compEntropyHighThr);
    cmd.AddValue("compDeadlineSlackMs",   "Adaptive: prefer LZ4 if (PDB-elapsed) < slack",
                                                                                compDeadlineSlackMs);
    cmd.AddValue("compPdbMs",             "PDB for the flow (ms); 0 = per-class default",
                                                                                compPdbMs);
    cmd.Parse(argc, argv);

    if (mobilityTrace.empty())
    {
        std::cerr << "traffic_class_compress_eval: --mobilityTrace is required\n";
        return 1;
    }
    if (maxNumTx < 1 || maxNumTx > 32)
    {
        std::cerr << "traffic_class_compress_eval: --maxNumTx must be in [1,32]\n";
        return 1;
    }
    if (psfchPeriod != 0 && psfchPeriod != 1 && psfchPeriod != 2 && psfchPeriod != 4)
    {
        std::cerr << "traffic_class_compress_eval: --psfchPeriod must be 0, 1, 2, or 4\n";
        return 1;
    }
    if (simTag.empty()) simTag = runId;
    numUEs = std::max(numUEs, 2u);

    TrafficClass tc;
    BandwidthPartInfo::Scenario baseScenario;
    CompressionController::Mode compModeEnum;
    try
    {
        tc           = ParseTrafficClass(trafficClassStr);
        baseScenario = ParseScenario(baseScenarioStr);
        compModeEnum = CompressionController::ParseMode(compMode);
    }
    catch (const std::exception& e)
    {
        std::cerr << "traffic_class_compress_eval: " << e.what() << "\n";
        return 1;
    }

    if (txNodeId >= numUEs)
    {
        std::cerr << "traffic_class_compress_eval: --txNodeId (" << txNodeId
                  << ") must be < --numUEs (" << numUEs << ")\n";
        return 1;
    }

    if (compPdbMs == 0)
    {
        compPdbMs = DefaultPdbMsForClass(trafficClassStr);
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
            std::cerr << "traffic_class_compress_eval: invalid --rri value\n";
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

    // ── 2. NR/SL stack ─────────────────────────────────────────────────────
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

    // ── 3. Single flow ─────────────────────────────────────────────────────
    Time rriTime = (rri > 0) ? MilliSeconds(static_cast<int64_t>(rri))
                              : DefaultRriForTrafficClass(tc);

    FlowDef fd;
    fd.label        = "tx_only";
    fd.port         = 8001;
    fd.priority     = 3;
    fd.rri          = rriTime;
    fd.dynamic      = (rriTime == Time(0));
    fd.pdb          = (rriTime > Time(0)) ? rriTime : MilliSeconds(compPdbMs);
    fd.rateKbps     = 100.0;
    fd.enabled      = true;
    fd.harqOverride = -1;
    fd.txNodes      = { txNodeId };
    fd.startTime    = Time(0);
    fd.stopTime     = Time(0);
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
    hooks.v2xKpi.SetRangeForV2xKpis(1000);

    ConnectPhyMacTraces(hooks);
    ConnectAppTraces(hooks, flows);

    // ── 5. CompressionController wiring per TX flow ────────────────────────
    // totalSubCh is conservatively computed from the BWP PRBs ≈ 106 at mu=1
    // (40 MHz, 30 kHz SCS), or 106 at mu=0 (20 MHz, 15 kHz SCS). Both round
    // down to floor(106/bwPRBs). We refresh once at setup since MCS+bwPRBs
    // are constant in this binary.
    const uint32_t totalSubCh = std::max(1u, 106u / std::max(1u, bwPRBs));
    const uint32_t tbCap      = EstimateTbBytes(mcs, bwPRBs, totalSubCh);

    NS_LOG_INFO("Compression setup: mode=" << compMode
                << " tbCap=" << tbCap << " bytes"
                << " totalSubCh=" << totalSubCh
                << " pdbMs=" << compPdbMs);

    std::vector<Ptr<CompressionController>> controllers;

    for (size_t f = 0; f < flows.defs.size(); ++f)
    {
        const uint16_t flowId = flows.defs[f].port;
        for (uint32_t a = 0; a < flows.clients[f].GetN(); ++a)
        {
            Ptr<Application> appBase = flows.clients[f].Get(a);
            Ptr<CustomTrafficApplication> app =
                DynamicCast<CustomTrafficApplication>(appBase);
            if (!app)
            {
                NS_LOG_WARN("Client app for flow " << flowId
                            << " is not a CustomTrafficApplication; "
                            << "compression disabled for this flow.");
                continue;
            }

            Ptr<CompressionController> ctrl = CreateObject<CompressionController>();
            ctrl->SetMode(compModeEnum);
            ctrl->SetTbSizeEstimator([tbCap]() { return tbCap; });
            ctrl->SetNackRateProvider([&hooks]() { return hooks.RecentNackRate(500.0); });
            ctrl->SetMaxDistanceProvider(
                [&ueNodes, txNodeId]() { return MaxRxDistanceFromTx(ueNodes, txNodeId); });
            ctrl->SetPdbMs(compPdbMs);
            ctrl->SetTrafficClassPattern(trafficClassStr);
            ctrl->SetSeed(seed);
            ctrl->SetTxNodeIdAndFlow(app->GetNode()->GetId(), flowId);
            ctrl->SetEntropyHighThr(compEntropyHighThr);
            ctrl->SetNackHighThr(compNackHighThr);
            ctrl->SetDistFarThr(compDistFarThr);
            ctrl->SetDeadlineSlackMs(compDeadlineSlackMs);

            // TX log callback: writes compressionLog and rlcFrag rows.
            Ptr<CompressionController> ctrlForLog = ctrl;
            ctrl->SetTxLogCb([&hooks, tbCap](double timeSec,
                                             uint32_t nodeId,
                                             uint16_t fid,
                                             uint32_t pktSeq,
                                             const CompressionController::Decision& d,
                                             uint32_t nSegmentsPost,
                                             uint32_t /*tb*/)
                            {
                                hooks.WriteCompressionLog(timeSec, "tx",
                                                          nodeId, fid, pktSeq,
                                                          d.tag.codec,
                                                          d.tag.origSize,
                                                          d.tag.compSize,
                                                          d.tag.encodeUs,
                                                          0,
                                                          d.entropyEst,
                                                          d.reason);
                                const uint32_t nPre =
                                    CompressionController::EstimateSegments(d.tag.origSize, tbCap);
                                hooks.WriteRlcFragLog(timeSec, nodeId, pktSeq,
                                                      d.tag.origSize,
                                                      d.tag.compSize,
                                                      nPre, nSegmentsPost, tbCap);
                            });

            // Provider runs the codec and returns wire bytes; Decorator reads
            // the cached tag from the controller and attaches it. CustomTraffic
            // app guarantees Provider→Decorator order within one packet.
            Ptr<CompressionController> ctrlCap = ctrl;
            app->SetSendBytesProvider(
                [ctrlCap](uint32_t targetBytes) -> std::vector<uint8_t>
                {
                    std::vector<uint8_t> payload = ctrlCap->SynthesisePayload(targetBytes);
                    CompressionController::Decision d = ctrlCap->Decide(payload);
                    return d.wireBytes;
                });
            app->SetTagDecorator(
                [ctrlCap](Ptr<Packet> pkt, uint32_t /*target*/, uint32_t /*wire*/)
                {
                    pkt->AddPacketTag(ctrlCap->GetLastTag());
                });

            controllers.push_back(ctrl);
        }
    }

    // ── 6. Run ─────────────────────────────────────────────────────────────
    double simStop = 0.0;
    for (size_t f = 0; f < flows.defs.size(); ++f)
        simStop = std::max(simStop, flows.appStart[f] + simTime + 0.5);
    Simulator::Stop(Seconds(simStop));
    Simulator::Run();

    // ── 7. Flush + summary CSV ─────────────────────────────────────────────
    Flush(hooks);

    if (!outRunCsv.empty())
    {
        std::ofstream r(outRunCsv);
        if (r.is_open())
        {
            r << "runId,seed,trafficClass,baseScenario,mcs,bwPRBs,psfchPeriod,"
                 "maxNumTx,harq,simTime,warmup,db_path,scheme,compMode,"
                 "compNackHighThr,compDistFarThr,compEntropyHighThr,"
                 "compDeadlineSlackMs,compPdbMs\n"
              << runId << ',' << seed << ',' << trafficClassStr << ','
              << baseScenarioStr << ',' << mcs << ',' << bwPRBs << ','
              << psfchPeriod << ',' << maxNumTx << ',' << (harqEnabled ? 1 : 0)
              << ',' << simTime << ',' << warmup << ',' << dbBase << ".db,"
              << "compress," << compMode << ','
              << compNackHighThr << ',' << compDistFarThr << ','
              << compEntropyHighThr << ',' << compDeadlineSlackMs << ','
              << compPdbMs << '\n';
        }
    }

    Simulator::Destroy();
    NS_LOG_INFO("Done: " << runId);
    return 0;
}
