// relay_l2id_compare.cc
//
// Unified relay-design comparison for NR-V2X mode-2 sidelink groupcast on a
// MOVING 9-vehicle platoon, with the MCD (Maximum Communication Distance) PSFCH
// feedback gate. One binary, four relay designs selected by --relayMode:
//
//   none      : 1-hop baseline. Leader groupcasts to all followers (L2ID 254,
//               HARQ). No relay.
//   sameL2    : 2-hop SAME-L2ID relay (the "storm" design). Leader serves ALL
//               followers on L2ID 254; the relay re-broadcasts on the SAME L2ID
//               254, so both hops share one HARQ feedback group -> reTX storm.
//   fixed     : 2-hop DISTINCT-L2ID relay with STRICT front/tail partition.
//               Leader serves the FRONT segment (nodes 1..relayNode) on L2ID 254;
//               the relay serves the TAIL (relayNode+1..N-1) on L2ID 255 with its
//               OWN HARQ feedback group -> feedback coupling removed (no storm),
//               but leader<->relay SB-SPS resource collisions remain.
//   broadcast : 2-hop DISTINCT-L2ID relay on a BROADCAST (no-PSFCH) hop-2 link.
//               Leader serves all followers (L2ID 254, HARQ); a boundary-elected
//               relay adds a redundant broadcast copy to the tail (L2ID 255, no
//               NACK / no retx) -> lowest hop-2 cost, no hop-2 reliability net.
//
// This binary REUSES the existing common/ modules unchanged (SameL2IdRelayApp,
// FixedRelayApp, BoundaryRelayApp, McrFeedbackGate, the NR-SL stack, flows, and
// KPI hooks). It edits no existing source. Scheduler is NR mode-2 SB-SPS only.
//
// Defaults are matched to results_relay_storm_eval (mu=2, 10 subchannels,
// PSFCH-4, fixed MCS-14, moving platoon, MCD-swept) so the four arms are
// directly comparable to the existing storm campaign.
//
// Optional --adaptiveMcs enables the worst-RX NACK-driven adaptive-MCS controller
// on the leader (the "strong" single-hop baseline), so the relay arms can be
// compared against MCS adaptation, not only against fixed MCS-14.
//
// Orchestration (mirrors relay_storm_eval, generalised over relayMode):
//   1. CLI + validation
//   2. InstallMobility (moving platoon, ConstantVelocityMobilityModel)
//   3. BuildSlStack    (20 MHz / mu=2 / 10 subch / PSFCH-4 / MCD gate / SB-SPS)
//   4. InstallGroupcastFlows (leader flow; rxNodes partitioned for fixed mode)
//   5. Install relay   (per --relayMode)
//   6. KpiHooks + trace connect (+ optional adaptive-MCS controller)
//   7. Simulator::Run() + PreFlush + Flush + run-summary CSV

#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
#include "common/kpi/v2x-kpi.h"
#include "common/mobility/mobility.h"
#include "common/relay/boundary_relay_app.h"
#include "common/relay/fixed_relay_app.h"
#include "common/relay/same_l2id_relay_app.h"
#include "common/stack/nr-sl-adaptive-mcs-controller.h"
#include "common/stack/nr-sl-adaptive-mcs-scheduler.h"
#include "common/stack/nr_sl_stack.h"

#include "ns3/core-module.h"
#include "ns3/log.h"
#include "ns3/nr-sl-ue-mac.h"
#include "ns3/nr-ue-net-device.h"

#include <cmath>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("RelayL2idCompare");

static BandwidthPartInfo::Scenario
ParseScenario(const std::string& s)
{
    if (s == "V2V_Highway")      return BandwidthPartInfo::V2V_Highway;
    if (s == "V2V_Urban")        return BandwidthPartInfo::V2V_Urban;
    if (s == "UMi_StreetCanyon") return BandwidthPartInfo::UMi_StreetCanyon;
    if (s == "UMa")              return BandwidthPartInfo::UMa;
    std::cerr << "relay_l2id_compare: unknown --scenario '" << s
              << "', defaulting to V2V_Highway\n";
    return BandwidthPartInfo::V2V_Highway;
}

int
main(int argc, char* argv[])
{
    // -----------------------------------------------------------------------
    // 1. CLI (defaults matched to relay_storm_eval)
    // -----------------------------------------------------------------------
    std::string runId         = "run0";
    uint32_t    seed          = 1;
    uint32_t    numUEs        = 9;       // 9-vehicle platoon

    // Platoon motion
    double      speedKmh      = 80.0;
    double      ivd           = 23.0;
    double      staticLaneY   = 0.0;
    double      staticFrontX  = 1000.0;
    double      antennaHeight = 1.5;

    // Radio resource (mu=2 -> 60 kHz SCS, 80 MHz legacy band, 10x10 PRB pool)
    uint32_t    mu            = 2;
    double      channelBwMHz  = 0.0;     // 0 = legacy auto-derive
    uint32_t    numSubchannels = 10;
    uint32_t    bwPRBs        = 10;
    uint32_t    mcs           = 14;
    uint8_t     maxNumTx      = 5;
    uint16_t    psfchPeriod   = 4;       // PSFCH every 4 slots (Option 2)
    bool        harqEnabled   = true;
    bool        shadowing     = true;
    std::string scenarioStr   = "V2V_Highway"; // V2V_Highway | V2V_Urban | UMi_StreetCanyon | UMa

    // MCR feedback gate (HARQ Option-1 only — Option-2 ignores per 3GPP TS 38.321)
    // Spec-faithful: HARQ Option-2 (Groupcast) gates PSFCH feedback by Zone-ID
    // membership (sl-ZoneConfig), NOT by distance. Since we don't model Zone-IDs
    // and our single-dstL2Id deployment has all UEs as group members, the
    // spec-faithful Option-2 has no distance filter.
    // Historical runs of this campaign used the now-deprecated MCD distance gate
    // applied unconditionally; numbers in current CSVs carry that bias.
    double      mcrMeters     = 150.0;

    // Relay design
    std::string relayMode     = "sameL2"; // none | sameL2 | fixed | broadcast
    uint32_t    relayNode     = 4;        // relay id / front-tail partition boundary
    uint16_t    relayPort     = 8101;     // hop-2 UDP port (fixed/broadcast)
    uint32_t    relayDstL2Id  = 255;      // hop-2 L2ID (fixed/broadcast)

    // Traffic (leader CAM)
    double      leaderPktBytes = 300.0;
    double      leaderPdbMs    = 10.0;
    double      leaderRriMs     = 10.0;

    // Optional adaptive MCS (worst-RX NACK-driven) on the leader
    bool        adaptiveMcs   = false;
    double      adaptDownThr  = 0.15;
    double      adaptUpThr    = 0.08;
    uint32_t    adaptWinMs    = 500;
    uint32_t    adaptEvalMs   = 500;

    // Sim control
    double      simTime       = 10.0;
    double      warmup        = 1.0;
    std::string outputDir     = "./";
    std::string simTag;

    // PSFCH KPI (per-opportunity ACK/NACK/SILENCE)
    bool        psfchKpi          = false;
    bool        psfchKpiSummaryOnly = false;
    std::string psfchKpiPath;

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId",         "Unique run identifier",                          runId);
    cmd.AddValue("seed",          "Random seed",                                    seed);
    cmd.AddValue("numUEs",        "Number of UE nodes (default 9)",                 numUEs);
    cmd.AddValue("speed",         "Platoon speed (km/h)",                           speedKmh);
    cmd.AddValue("ivd",           "Inter-vehicle distance (m)",                     ivd);
    cmd.AddValue("laneY",         "Lane y-coordinate (m)",                          staticLaneY);
    cmd.AddValue("frontX",        "Initial x of lead vehicle (m)",                  staticFrontX);
    cmd.AddValue("antennaHeight", "Antenna height (m)",                             antennaHeight);
    cmd.AddValue("mu",            "Numerology (2=60kHz)",                           mu);
    cmd.AddValue("channelBwMHz",  "SLRP channel bandwidth (MHz, 0=legacy)",         channelBwMHz);
    cmd.AddValue("numSubchannels","Number of subchannels in resource pool",         numSubchannels);
    cmd.AddValue("bwPRBs",        "Subchannel size in PRBs",                        bwPRBs);
    cmd.AddValue("mcs",           "Fixed MCS index (initial MCS if adaptiveMcs)",   mcs);
    cmd.AddValue("maxNumTx",      "Max PSSCH TX per TB (1..32)",                    maxNumTx);
    cmd.AddValue("psfchPeriod",   "PSFCH period in slots (4=Option-2)",             psfchPeriod);
    cmd.AddValue("harq",          "Enable HARQ (true=Option-2 ACK+NACK)",           harqEnabled);
    cmd.AddValue("shadowing",     "Enable shadowing",                               shadowing);
    cmd.AddValue("scenario",      "3GPP scenario: V2V_Highway|V2V_Urban|UMi_StreetCanyon|UMa", scenarioStr);
    cmd.AddValue("mcr",           "Min communication range for PSFCH gate (m, Option-1 only)", mcrMeters);
    cmd.AddValue("relayMode",     "Relay design: none | sameL2 | fixed | broadcast", relayMode);
    cmd.AddValue("relayNode",     "Relay node id / front-tail partition boundary",  relayNode);
    cmd.AddValue("relayPort",     "Hop-2 UDP port (fixed/broadcast)",               relayPort);
    cmd.AddValue("relayDstL2Id",  "Hop-2 groupcast L2ID (fixed/broadcast)",         relayDstL2Id);
    cmd.AddValue("pktBytes",      "Leader packet size (bytes)",                     leaderPktBytes);
    cmd.AddValue("pdb",           "Leader PDB (ms)",                                leaderPdbMs);
    cmd.AddValue("rri",           "Leader RRI (ms)",                                leaderRriMs);
    cmd.AddValue("adaptiveMcs",   "Enable worst-RX NACK-driven adaptive MCS on leader", adaptiveMcs);
    cmd.AddValue("adaptDownThr",  "Adaptive MCS: worst-RX NACK rate -> step down",  adaptDownThr);
    cmd.AddValue("adaptUpThr",    "Adaptive MCS: mean-RX NACK rate -> step up",     adaptUpThr);
    cmd.AddValue("adaptWinMs",    "Adaptive MCS: sliding-window length (ms)",       adaptWinMs);
    cmd.AddValue("adaptEvalMs",   "Adaptive MCS: evaluation period (ms)",           adaptEvalMs);
    cmd.AddValue("simTime",       "Traffic duration (s)",                           simTime);
    cmd.AddValue("warmup",        "Warm-up seconds discarded by V2xKpi",            warmup);
    cmd.AddValue("outputDir",     "Output directory for KPI SQLite DB",             outputDir);
    cmd.AddValue("simTag",        "DB filename tag (default = runId)",              simTag);
    cmd.AddValue("psfchKpi",        "Record per-PSFCH-opportunity ACK/NACK/SILENCE", psfchKpi);
    cmd.AddValue("psfchKpiSummaryOnly", "Emit only summary CSV (no per-event rows)", psfchKpiSummaryOnly);
    cmd.AddValue("psfchKpiPath",     "Override PSFCH KPI CSV path (default = outputDir/simTag_psfch_opps.csv)", psfchKpiPath);
    cmd.Parse(argc, argv);

    // -- validation ----------------------------------------------------------
    if (simTag.empty()) simTag = runId;
    numUEs = std::max(numUEs, 2u);

    if (relayMode != "none" && relayMode != "sameL2" &&
        relayMode != "fixed" && relayMode != "broadcast")
    {
        std::cerr << "relay_l2id_compare: invalid --relayMode '" << relayMode
                  << "' (use none|sameL2|fixed|broadcast)\n";
        return 1;
    }
    const bool relayOn = (relayMode != "none");
    if (relayOn && (relayNode < 1 || relayNode >= numUEs))
    {
        std::cerr << "relay_l2id_compare: relayNode=" << relayNode
                  << " must satisfy 1 <= relayNode < numUEs=" << numUEs << "\n";
        return 1;
    }
    // fixed/broadcast partition leaves a tail behind the relay.
    if ((relayMode == "fixed" || relayMode == "broadcast") &&
        relayNode > numUEs - 2)
    {
        std::cerr << "relay_l2id_compare: " << relayMode << " needs a non-empty tail "
                  << "(relayNode <= numUEs-2 = " << (numUEs - 2) << ")\n";
        return 1;
    }
    if (psfchPeriod != 0 && psfchPeriod != 1 &&
        psfchPeriod != 2 && psfchPeriod != 4)
    {
        std::cerr << "relay_l2id_compare: --psfchPeriod must be 0,1,2 or 4\n";
        return 1;
    }
    if (adaptiveMcs && psfchPeriod == 0)
    {
        std::cerr << "relay_l2id_compare: --adaptiveMcs requires --psfchPeriod > 0\n";
        return 1;
    }

    RngSeedManager::SetSeed(1);
    RngSeedManager::SetRun(seed);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    const double speedMps = speedKmh / 3.6;

    NS_LOG_INFO("=== relay_l2id_compare ==="
                << " mode=" << relayMode
                << " relayNode=" << (relayOn ? std::to_string(relayNode) : "none")
                << " speed=" << speedKmh << " km/h ivd=" << ivd << " m"
                << " mcr=" << mcrMeters << " m (Option-1 only) adaptiveMcs=" << adaptiveMcs
                << " seed=" << seed);

    // -----------------------------------------------------------------------
    // 2. Moving platoon mobility (matched to relay_storm_eval)
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
    // 3. NR-SL stack: mu=2 / 10 subch / PSFCH-4 / HARQ Option-2 / SB-SPS
    //    Spec-faithful: no MCR gate for Option-2 (Zone-ID mechanism not
    //    modeled, all UEs are group members so no gate = no filter).
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
    sp.scenario         = ParseScenario(scenarioStr);
    sp.adaptiveMcs      = adaptiveMcs;      // installs adaptive scheduler when true
    sp.schedulerMode    = "sbsps";          // NR mode-2 SB-SPS only (scope)
    sp.harqOption       = 2;                // HARQ Option-2 (ACK/NACK groupcast)
    sp.mcrMeters        = 0.0;              // gate skipped for Option-2 per spec
    sp.recordPsfchKpi   = psfchKpi;
    sp.psfchKpiSummaryOnly = psfchKpiSummaryOnly;
    if (psfchKpiPath.empty())
    {
        sp.psfchKpiPath = outputDir + "/" + simTag + "_psfch_opps.csv";
    }
    else
    {
        sp.psfchKpiPath = psfchKpiPath;
    }
    sp.rriList          = {static_cast<uint16_t>(std::lround(leaderRriMs))};

    SlStackCtx ctx = BuildSlStack(sp, geom, ueNodes);

    // -----------------------------------------------------------------------
    // 4. Leader groupcast flow (node 0 TX).
    //    fixed mode partitions the group: leader serves the FRONT (1..relayNode).
    //    none / sameL2 / broadcast: leader serves ALL followers (1..N-1).
    // -----------------------------------------------------------------------
    const uint32_t lastLeaderRx =
        (relayMode == "fixed") ? relayNode : (numUEs - 1);
    std::vector<uint32_t> leaderRx;
    for (uint32_t i = 1; i <= lastLeaderRx; ++i) leaderRx.push_back(i);

    FlowDef leaderFlow;
    leaderFlow.label        = "leader_cam";
    leaderFlow.port         = 8001;
    leaderFlow.priority     = 1;
    leaderFlow.rri          = MilliSeconds(static_cast<uint32_t>(leaderRriMs));
    leaderFlow.dynamic      = false;  // SPS
    leaderFlow.pdb          = MilliSeconds(static_cast<uint32_t>(leaderPdbMs));
    leaderFlow.rateKbps     = (leaderPktBytes * 8.0) / (leaderRriMs / 1000.0) / 1000.0;
    leaderFlow.enabled      = true;
    leaderFlow.harqOverride = harqEnabled ? 1 : 0;
    leaderFlow.txNodes      = {0};
    leaderFlow.rxNodes      = leaderRx;
    leaderFlow.dstL2Id      = 254;
    leaderFlow.mcs          = -1;     // use global --mcs (or adaptive)
    leaderFlow.trafficClass = TrafficClass::OnOffConstant;

    int64_t stream = ctx.stream;
    FlowResult flows = InstallGroupcastFlows(ueNodes, ctx,
                                             {leaderFlow},
                                             simTime,
                                             harqEnabled,
                                             stream);

    const size_t   leaderIdx  = 0;            // single leader flow
    const uint16_t leaderPort = leaderFlow.port;

    // -----------------------------------------------------------------------
    // 5. Relay install (per --relayMode). All apps come from common/relay/.
    // -----------------------------------------------------------------------
    Ptr<SameL2IdRelayApp>              sameApp;
    Ptr<FixedRelayApp>                 fixedApp;
    std::vector<Ptr<BoundaryRelayApp>> boundApps;

    if (relayMode == "sameL2")
    {
        // Relay re-broadcasts on the SAME L2ID 254 -> shared feedback group.
        sameApp = InstallSameL2IdRelay(ueNodes, ctx, flows,
                                       relayNode, 0 /*flowIdx*/, simTime);
        NS_LOG_INFO("same-L2ID relay installed on node " << relayNode);
    }
    else if (relayMode == "fixed")
    {
        // Distinct L2ID 255, strict front/tail partition, separate feedback group.
        Ptr<Application> relayLeaderSink;
        for (uint32_t a = 0; a < flows.servers[leaderIdx].GetN(); ++a)
        {
            Ptr<Application> s = flows.servers[leaderIdx].Get(a);
            if (s->GetNode()->GetId() == relayNode) { relayLeaderSink = s; break; }
        }
        if (!relayLeaderSink)
        {
            std::cerr << "relay_l2id_compare: relayNode " << relayNode
                      << " is not a front (Group-A) receiver\n";
            return 1;
        }

        std::vector<uint32_t> tailNodes;
        for (uint32_t n = relayNode + 1; n < numUEs; ++n) tailNodes.push_back(n);

        const Time relayPdb = leaderFlow.pdb;
        fixedApp = InstallFixedRelay(ueNodes, ctx, flows, relayNode, tailNodes,
                                     relayLeaderSink, leaderPort, relayPort,
                                     relayDstL2Id, relayPdb, leaderFlow.rri,
                                     leaderFlow.dynamic, harqEnabled, simTime);
        NS_LOG_INFO("fixed distinct-L2ID relay on node " << relayNode
                    << " -> tail " << (relayNode + 1) << ".." << (numUEs - 1));
    }
    else if (relayMode == "broadcast")
    {
        // Distinct L2ID 255 on a Broadcast (no-PSFCH) hop-2 link, boundary-elected
        // at the relayNode position so it is comparable to the fixed relay.
        const double boundaryM = static_cast<double>(relayNode) * ivd;
        boundApps = InstallRelayGroup(ueNodes, ctx, flows, 0 /*txNodeId*/,
                                      flows.servers[leaderIdx], leaderPort,
                                      relayPort, relayDstL2Id, boundaryM,
                                      leaderFlow.pdb, simTime);
        NS_LOG_INFO("broadcast relay group at boundary " << boundaryM << " m ("
                    << boundApps.size() << " candidate apps)");
    }

    // -----------------------------------------------------------------------
    // 6. KPI hooks
    // -----------------------------------------------------------------------
    const std::string dbPath = outputDir + "/" + simTag;
    KpiHooks kpi(dbPath, "" /*no per-packet CSV for sweeps*/);

    ConnectPhyMacTraces(kpi);
    ConnectAppTraces(kpi, flows);

    // Relay per-packet timing (sameL2 / fixed) -> relayTiming table.
    auto timingCb = [&kpi](uint32_t seq, uint16_t port, uint32_t nodeId,
                           double genSec, double decodeSec, double sendSec, int fwd) {
        kpi.WriteRelayTiming(seq, port, nodeId, genSec, decodeSec, sendSec, fwd);
    };
    if (sameApp)  sameApp->SetTimingCb(timingCb);
    if (fixedApp) fixedApp->SetTimingCb(timingCb);

    // Boundary-relay election log (broadcast) -> relaySelect table.
    for (auto& app : boundApps)
    {
        app->SetLogCb([&kpi](double t, double b, uint32_t n, double d,
                             uint32_t s, int fwd) {
            kpi.WriteRelaySelect(t, b, n, d, s, fwd);
        });
    }

    kpi.v2xKpi.SetDbPath(dbPath);
    kpi.v2xKpi.SetTxAppDuration(simTime);
    kpi.v2xKpi.SetRangeForV2xKpis(0); // no PSFCH gate for Option-2 (spec-faithful)

    // -----------------------------------------------------------------------
    // 6b. Optional worst-RX NACK-driven adaptive MCS controller (leader = node 0).
    //     Provides the "strong" single-hop baseline (C4). nackRate mode only;
    //     requires sp.adaptiveMcs=true so BuildSlStack installed the adaptive
    //     scheduler.
    // -----------------------------------------------------------------------
    Ptr<NrSlAdaptiveMcsController> adaptCtrl;
    if (adaptiveMcs)
    {
        Ptr<Node> txNode = ueNodes.Get(0);
        auto txDev = txNode->GetDevice(0)->GetObject<NrUeNetDevice>();
        NS_ABORT_MSG_IF(!txDev, "adaptiveMcs: node 0 device 0 is not NrUeNetDevice");
        auto nrSlMac = txDev->GetMac(0)->GetObject<NrSlUeMac>();
        NS_ABORT_MSG_IF(!nrSlMac, "adaptiveMcs: could not cast MAC to NrSlUeMac");
        PointerValue schedPv;
        nrSlMac->GetAttribute("NrSlUeMacScheduler", schedPv);
        auto adaptSched = schedPv.Get<NrSlUeMacScheduler>()
                              ->GetObject<NrSlAdaptiveMcsScheduler>();
        NS_ABORT_MSG_IF(!adaptSched,
                        "adaptiveMcs: scheduler is not NrSlAdaptiveMcsScheduler");

        adaptCtrl = CreateObject<NrSlAdaptiveMcsController>();
        adaptCtrl->SetScheduler(adaptSched);
        adaptCtrl->SetInitialMcs(static_cast<uint8_t>(mcs));
        adaptCtrl->SetWindowMs(adaptWinMs);
        adaptCtrl->SetEvalIntervalMs(adaptEvalMs);
        adaptCtrl->SetDownThreshold(adaptDownThr);
        adaptCtrl->SetUpThreshold(adaptUpThr);
        adaptCtrl->SetUpHysteresis(5);
        adaptCtrl->SetPerFlowAdaptation(false);
        adaptCtrl->SetMode(NrSlAdaptiveMcsController::Mode::NackRate);
        adaptCtrl->SetEtpVariant(NrSlAdaptiveMcsController::EtpVariant::Average);
        adaptCtrl->SetMcsCandidates(
            {28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14,
             13, 12, 11, 10, 7, 6, 5, 4, 3, 2, 1, 0});

        Config::Connect(
            "/NodeList/*/DeviceList/0/$ns3::NrUeNetDevice"
            "/ComponentCarrierMapUe/*/NrUeMac/$ns3::NrSlUeMac"
            "/NrSlUeMacHarq/RxHarqFeedback",
            MakeCallback(&NrSlAdaptiveMcsController::OnHarqFeedback, adaptCtrl));

        adaptCtrl->SetAdaptLogCb(
            [&kpi](double timeMs, double metric, uint8_t prevMcs, uint8_t newMcs,
                   const std::string& decision, uint32_t ackCount, uint32_t nackCount,
                   double windowStartMs, double windowEndMs) {
                kpi.WriteAdaptLog(timeMs, metric, prevMcs, newMcs, decision,
                                  ackCount, nackCount, windowStartMs, windowEndMs);
            });
        adaptCtrl->Start();
        NS_LOG_INFO("adaptive MCS (nackRate) controller started on leader");
    }

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
        sf << "runId,seed,relayMode,relayNode,relayDstL2Id,numUEs,scenario,speedKmh,ivd,mcrMeters,"
              "mu,numSubchannels,bwPRBs,mcs,adaptiveMcs,maxNumTx,psfchPeriod,simTime,harqOption,psfchKpi\n";
        sf << runId << "," << seed << "," << relayMode << ","
           << (relayOn ? static_cast<int>(relayNode) : -1) << ","
           << ((relayMode == "fixed" || relayMode == "broadcast")
                   ? static_cast<int>(relayDstL2Id) : 254) << ","
           << numUEs << "," << scenarioStr << ","
           << speedKmh << "," << ivd << "," << mcrMeters << ","
           << mu << "," << numSubchannels << "," << bwPRBs << "," << mcs << ","
           << (adaptiveMcs ? "true" : "false") << ","
           << static_cast<int>(maxNumTx) << "," << psfchPeriod << "," << simTime << ","
           << 2 << "," << (psfchKpi ? "true" : "false") << "\n";
    }

    NS_LOG_INFO("relay_l2id_compare done. DB: " << dbPath << ".db");
    return 0;
}
