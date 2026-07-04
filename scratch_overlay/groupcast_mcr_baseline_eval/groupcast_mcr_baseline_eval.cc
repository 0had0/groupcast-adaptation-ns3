// groupcast_mcr_baseline_eval.cc
//
// SINGLE-HOP BASELINE (NO RELAY) for the NR-V2X mode-2 sidelink groupcast
// platoon study. Mirrors the system model of relay_l2id_compare /
// relay_storm_eval so results are directly comparable, but sweeps a 5-D grid
// over (platoon size N, HARQ feedback option, Minimum Communication Range
// MCR, traffic class, speed-IVD operating point).
//
// Reuses common/ modules UNCHANGED:
//   - mobility (moving platoon, ConstantVelocityMobilityModel)
//   - stack/nr_sl_stack (SLRP, SB-SPS, PSFCH, MCD gate via mcdMeters)
//   - mcd/mcr_nack_only_gate (HARQ Option-1 ACK-drop shim — additive)
//   - flows/groupcast_flows (FlowDef + TrafficClass + HarqMode)
//   - kpi/kpi_hooks, kpi/v2x-kpi (KpiHooks, V2xKpi, ConnectPhyMacTraces,
//     ConnectAppTraces; PreFlush / Flush)
//
// Knobs (CLI):
//   --numUEs N          : platoon size (8 | 10 | 16 | 20)
//   --harqOption 1|2    : HARQ feedback option (1 = NACK-only via
//                         mcr_nack_only_gate; 2 = ACK/NACK default).
//                         When 1, the MCR NACK-only gate is installed AFTER
//                         BuildSlStack and the per-flow HarqMode is forced
//                         to NackOnly (GroupcastNegativeOnly castType).
//   --mcrMeters M       : MCR distance (m); receivers beyond MCR are silent
//                         (drop ALL feedback). Same knob as --mcd in the
//                         relay studies, renamed to make the operator
//                         intent explicit.
//   --traffic tc        : traffic class (periodic1|periodic2|aperiodic1|
//                         aperiodic2). Mapped to the canonical FlowDef + the
//                         matching TrafficClass from common/flows.
//   --speed S --ivd D   : speed (km/h) and inter-vehicle distance (m). PAIRED
//                         per the 1-s safe headway, swept across the 7
//                         (speed,ivd) operating points from
//                         relay_storm_eval/run_relay_storm.sh.
//   --seed N            : random seed.
//
// Plus all standard radio / geometry / sim-time args from relay_l2id_compare.
//
// Orchestration (mirrors relay_l2id_compare relayMode="none" path):
//   1. CLI + validation
//   2. InstallMobility (moving platoon, ConstantVelocityMobilityModel)
//   3. BuildSlStack    (mu=2, 10 subchannels, PSFCH-4, MCD/MCR gate)
//   4. InstallGroupcastFlows (leader flow to rxNodes = 1..N-1)
//   5. (when --harqOption=1) InstallMcrNackOnlyGate
//   6. KpiHooks + trace connect
//   7. Simulator::Run() + PreFlush + Flush + run-summary CSV

#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
#include "common/kpi/v2x-kpi.h"
#include "common/mcd/mcd_feedback_gate.h"
#include "common/mcd/mcr_nack_only_gate.h"
#include "common/mobility/mobility.h"
#include "common/stack/nr_sl_stack.h"

#include "ns3/core-module.h"
#include "ns3/log.h"

#include <cmath>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("GroupcastMcrBaselineEval");

namespace
{

// Map --traffic string -> TrafficClass. Mirrors the keys accepted by
// ParseFlowsSpec (see common/flows/groupcast_flows.cc). We construct the
// FlowDef directly to avoid the parser for the leader flow.
TrafficClass
ParseTrafficClass(const std::string& s)
{
    if (s == "onoff")      return TrafficClass::OnOffConstant;
    if (s == "periodic1")  return TrafficClass::Periodic1;
    if (s == "periodic2")  return TrafficClass::Periodic2;
    if (s == "aperiodic1") return TrafficClass::Aperiodic1;
    if (s == "aperiodic2") return TrafficClass::Aperiodic2;
    std::cerr << "groupcast_mcr_baseline_eval: unknown --traffic '"
              << s << "', defaulting to onoff (OnOffConstant)\n";
    return TrafficClass::OnOffConstant;
}

// Traffic-class defaults (RRI, pdb, rateKbps, packetBytes) for the leader
// flow. The semantic of each class (sizes, distributions) lives in
// common/traffic/custom-traffic-application.cc; we only choose the RRI/PDB
// here so the SPS reservation and PDB-based app behavior are sane. For
// OnOffConstant (no periodic/aperiodic) we still need pktBytes/rateKbps;
// the harness uses these as informational / OnOff defaults.
struct TrafficProfile
{
    Time rri;
    Time pdb;
    uint32_t pktBytes;
};

TrafficProfile
ProfileFor(TrafficClass tc)
{
    switch (tc)
    {
        case TrafficClass::Periodic1:     return {MilliSeconds(50),  MilliSeconds(50),  300};
        case TrafficClass::Periodic2:     return {MilliSeconds(10),  MilliSeconds(10),  880};
        case TrafficClass::Periodic3:     return {MilliSeconds(30),  MilliSeconds(30),  45000};
        case TrafficClass::Aperiodic1:    return {MilliSeconds(50),  MilliSeconds(50),  1100};
        case TrafficClass::Aperiodic2:    return {MilliSeconds(20),  MilliSeconds(20),  20000};
        case TrafficClass::OnOffConstant:
        default:                          return {MilliSeconds(100), MilliSeconds(100), 300};
    }
}

} // namespace

int
main(int argc, char* argv[])
{
    // -----------------------------------------------------------------------
    // 1. CLI
    // -----------------------------------------------------------------------
    std::string runId        = "run0";
    uint32_t    seed         = 1;

    // Platoon
    uint32_t    numUEs       = 9;          // overridden via --numUEs
    double      speedKmh     = 80.0;
    double      ivd          = 25.0;
    double      staticLaneY  = 0.0;
    double      staticFrontX = 1000.0;
    double      antennaHeight = 1.5;

    // Radio resource (matched to relay_l2id_compare / relay_storm_eval)
    uint32_t    mu           = 2;          // 60 kHz SCS
    double      channelBwMHz = 0.0;        // 0 = legacy auto-derive (80 MHz)
    uint32_t    numSubchannels = 10;
    uint32_t    bwPRBs       = 10;
    uint32_t    mcs          = 14;
    uint8_t     maxNumTx     = 5;
    uint16_t    psfchPeriod  = 4;          // PSFCH every 4 slots
    bool        shadowing    = true;
    std::string scenarioStr  = "V2V_Highway";

    // MCR + HARQ feedback option
    double      mcrMeters    = 150.0;      // feedback scope (replaces MCD)
    uint32_t    harqOption   = 2;          // 1 = NACK-only, 2 = ACK/NACK

    // Traffic
    std::string trafficStr   = "periodic2";
    // (packetBytes / rateKbps are derived from the traffic profile below)

    // Sim control
    double      simTime      = 5.0;
    double      warmup       = 1.0;
    std::string outputDir    = "./";
    std::string simTag;

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId",         "Unique run identifier",                              runId);
    cmd.AddValue("seed",          "Random seed",                                        seed);
    cmd.AddValue("numUEs",        "Platoon size (number of UEs incl. leader)",           numUEs);
    cmd.AddValue("speed",         "Platoon speed (km/h)",                               speedKmh);
    cmd.AddValue("ivd",           "Inter-vehicle distance (m)",                         ivd);
    cmd.AddValue("laneY",         "Lane y-coordinate (m)",                              staticLaneY);
    cmd.AddValue("frontX",        "Initial x of lead vehicle (m)",                      staticFrontX);
    cmd.AddValue("antennaHeight", "Antenna height (m)",                                 antennaHeight);
    cmd.AddValue("mu",            "Numerology (2=60 kHz)",                              mu);
    cmd.AddValue("channelBwMHz",  "SLRP channel bandwidth (MHz, 0=legacy)",              channelBwMHz);
    cmd.AddValue("numSubchannels","Number of subchannels in resource pool",             numSubchannels);
    cmd.AddValue("bwPRBs",        "Subchannel size in PRBs",                            bwPRBs);
    cmd.AddValue("mcs",           "Fixed MCS index",                                    mcs);
    cmd.AddValue("maxNumTx",      "Max PSSCH TX per TB (1..32)",                        maxNumTx);
    cmd.AddValue("psfchPeriod",   "PSFCH period in slots (4=Option-2)",                 psfchPeriod);
    cmd.AddValue("shadowing",     "Enable shadowing",                                   shadowing);
    cmd.AddValue("scenario",      "3GPP scenario: V2V_Highway|V2V_Urban|UMi_StreetCanyon|UMa",
                                                                                        scenarioStr);
    cmd.AddValue("mcr",           "Minimum Communication Range (MCR) for PSFCH gate (m)", mcrMeters);
    cmd.AddValue("harqOption",    "HARQ feedback option (1=NACK-only, 2=ACK/NACK)",     harqOption);
    cmd.AddValue("traffic",       "Traffic class: periodic1|periodic2|aperiodic1|aperiodic2",
                                                                                        trafficStr);
    cmd.AddValue("simTime",       "Traffic duration (s)",                               simTime);
    cmd.AddValue("warmup",        "Warm-up seconds discarded by V2xKpi",                warmup);
    cmd.AddValue("outputDir",     "Output directory for KPI SQLite DB",                 outputDir);
    cmd.AddValue("simTag",        "DB filename tag (default = runId)",                  simTag);
    cmd.Parse(argc, argv);

    // -- validation ---------------------------------------------------------
    if (simTag.empty()) simTag = runId;
    numUEs = std::max(numUEs, 2u);
    if (harqOption != 1 && harqOption != 2)
    {
        std::cerr << "groupcast_mcr_baseline_eval: --harqOption must be 1 or 2\n";
        return 1;
    }
    if (mcrMeters < 0.0)
    {
        std::cerr << "groupcast_mcr_baseline_eval: --mcr must be >= 0\n";
        return 1;
    }
    if (psfchPeriod != 0 && psfchPeriod != 1 && psfchPeriod != 2 && psfchPeriod != 4)
    {
        std::cerr << "groupcast_mcr_baseline_eval: --psfchPeriod must be 0,1,2 or 4\n";
        return 1;
    }
    if (harqOption == 1 && psfchPeriod == 0)
    {
        std::cerr << "groupcast_mcr_baseline_eval: --harqOption=1 requires --psfchPeriod>0\n";
        return 1;
    }

    RngSeedManager::SetSeed(1);
    RngSeedManager::SetRun(seed);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    const double speedMps = speedKmh / 3.6;

    NS_LOG_INFO("=== groupcast_mcr_baseline_eval ==="
                << " N=" << numUEs
                << " speed=" << speedKmh << " km/h"
                << " ivd=" << ivd << " m"
                << " mcr=" << mcrMeters << " m"
                << " harqOption=" << harqOption
                << " traffic=" << trafficStr
                << " seed=" << seed);

    // -----------------------------------------------------------------------
    // 2. Moving platoon mobility (matched to relay_storm_eval / relay_l2id_compare)
    // -----------------------------------------------------------------------
    MobConfig mobCfg;
    mobCfg.numUEs          = numUEs;
    mobCfg.seed            = seed;
    mobCfg.staticPlatoon   = true;          // fixed spacing...
    mobCfg.ivd             = ivd;
    mobCfg.staticLaneY     = staticLaneY;
    mobCfg.staticFrontX    = staticFrontX;
    mobCfg.antennaHeight   = antennaHeight;
    mobCfg.platoonSpeedMps = speedMps;      // ...> 0 -> ConstantVelocityMobilityModel

    MobResult mob = InstallMobility(mobCfg);
    const NodeContainer& ueNodes = mob.ueNodes;

    // -----------------------------------------------------------------------
    // 3. NR-SL stack: mu=2 / 10 subch / PSFCH-4 / MCR gate / SB-SPS
    // -----------------------------------------------------------------------
    GeomChannelCfg geom;
    geom.blockerLengthM = 4.5;
    geom.blockerWidthM  = 2.0;

    SlStackParams sp;
    sp.mu               = mu;
    sp.bwPRBs           = bwPRBs;
    sp.channelBwMHz     = channelBwMHz;
    sp.numSubchannels   = numSubchannels;
    sp.mcs              = mcs;
    sp.seed             = seed;
    sp.maxNumTx         = static_cast<uint8_t>(maxNumTx);
    sp.psfchPeriod      = psfchPeriod;
    sp.shadowingEnabled = shadowing;
    // Scenario parsing — keep the same enum mapping as relay_l2id_compare.
    sp.scenario         = (scenarioStr == "V2V_Urban")        ? BandwidthPartInfo::V2V_Urban
                        : (scenarioStr == "UMi_StreetCanyon") ? BandwidthPartInfo::UMi_StreetCanyon
                        : (scenarioStr == "UMa")               ? BandwidthPartInfo::UMa
                                                               : BandwidthPartInfo::V2V_Highway;
    sp.adaptiveMcs      = false;            // fixed MCS sweep
    sp.schedulerMode    = "sbsps";          // NR mode-2 SB-SPS only (scope)
    sp.mcdMeters        = mcrMeters;        // MCD gate is the MCR scope gate.

    // RRI list from the traffic profile (single leader RRI).
    const TrafficClass tc = ParseTrafficClass(trafficStr);
    const TrafficProfile prof = ProfileFor(tc);
    sp.rriList = {static_cast<uint16_t>(prof.rri.GetMilliSeconds())};

    SlStackCtx ctx = BuildSlStack(sp, geom, ueNodes);

    // -----------------------------------------------------------------------
    // 4. Leader groupcast flow: node 0 TX → all followers (rxNodes = 1..N-1)
    // -----------------------------------------------------------------------
    std::vector<uint32_t> allFollowers;
    for (uint32_t i = 1; i < numUEs; ++i) allFollowers.push_back(i);

    FlowDef leaderFlow;
    leaderFlow.label        = "leader_" + trafficStr;
    leaderFlow.port         = 8001;
    leaderFlow.priority     = 1;
    leaderFlow.rri          = prof.rri;
    leaderFlow.dynamic      = false;        // SPS
    leaderFlow.pdb          = prof.pdb;
    leaderFlow.rateKbps     = (prof.pktBytes * 8.0) / (prof.rri.GetSeconds() * 1000.0);
    leaderFlow.enabled      = true;
    // Per-flow HARQ: Option-1 forces NackOnly regardless of global --harq;
    // Option-2 leaves the global default in place (legacy behavior preserved).
    leaderFlow.harqOverride = (harqOption == 2) ? 1 : 0;
    leaderFlow.harqMode     = (harqOption == 1) ? HarqMode::NackOnly : HarqMode::Default;
    leaderFlow.txNodes      = {0};
    leaderFlow.rxNodes      = allFollowers;
    leaderFlow.dstL2Id      = 254;
    leaderFlow.mcs          = -1;           // use global --mcs
    leaderFlow.trafficClass = tc;

    int64_t stream = ctx.stream;
    FlowResult flows = InstallGroupcastFlows(ueNodes, ctx,
                                             {leaderFlow},
                                             simTime,
                                             /*harqEnabledDefault=*/ true,
                                             stream);

    // -----------------------------------------------------------------------
    // 5. (harqOption=1) Install MCR NACK-only gate on top of the MCD gate.
    //    BuildSlStack already wired InstallMcdFeedbackGate(ctx.ueNetDev,
    //    sp.mcdMeters). We re-bind the spectrum-phy PSFCH callback to the
    //    NACK-only shim, which:
    //      - drops feedback beyond MCR (same as MCD gate, redundant here)
    //      - drops ACK feedback inside MCR (Option-1 silent-on-success)
    //      - forwards NACK feedback inside MCR to NrUePhy::EnqueueSlHarqFeedback
    //    When harqOption=2, skip this step and keep the MCD-only gate.
    // -----------------------------------------------------------------------
    if (harqOption == 1)
    {
        InstallMcrNackOnlyGate(ctx.ueNetDev, mcrMeters, /*dropAcks=*/ true);
        NS_LOG_INFO("MCR NACK-only gate installed (Option-1) on top of MCD gate");
    }

    // -----------------------------------------------------------------------
    // 6. KPI hooks
    // -----------------------------------------------------------------------
    const std::string dbPath = outputDir + "/" + simTag;
    KpiHooks kpi(dbPath, "" /*no per-packet CSV for sweeps*/);

    ConnectPhyMacTraces(kpi);
    ConnectAppTraces(kpi, flows);

    kpi.v2xKpi.SetDbPath(dbPath);
    kpi.v2xKpi.SetTxAppDuration(simTime);
    kpi.v2xKpi.SetRangeForV2xKpis(0); // PSFCH-level MCR gate handles range.

    // -----------------------------------------------------------------------
    // 7. Run
    // -----------------------------------------------------------------------
    NS_LOG_INFO("Starting simulation, simTime=" << simTime << " s");
    Simulator::Stop(Seconds(2.0 + simTime + 1.0));
    Simulator::Run();

    PreFlush(kpi);          // snapshot node positions before NodeList is cleared
    Simulator::Destroy();
    Flush(kpi);

    // One-line run summary CSV for the sweep aggregator.
    if (!outputDir.empty())
    {
        const std::string summaryPath = outputDir + "/" + simTag + "_summary.csv";
        std::ofstream sf(summaryPath);
        sf << "runId,seed,numUEs,speedKmh,ivd,mcrMeters,harqOption,traffic,"
              "mu,numSubchannels,bwPRBs,mcs,psfchPeriod,simTime\n";
        sf << runId << "," << seed << "," << numUEs << ","
           << speedKmh << "," << ivd << "," << mcrMeters << ","
           << harqOption << "," << trafficStr << ","
           << mu << "," << numSubchannels << "," << bwPRBs << "," << mcs << ","
           << static_cast<int>(psfchPeriod) << "," << simTime << "\n";
    }

    NS_LOG_INFO("groupcast_mcr_baseline_eval done. DB: " << dbPath << ".db");
    return 0;
}