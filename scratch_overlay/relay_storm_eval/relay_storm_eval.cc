// relay_storm_eval.cc
//
// 9-vehicle moving platoon NR-V2X relay storm study.
//
// Topology:
//   Node 0 = leader (TX), nodes 1..8 = followers (RX).
//   One relay (node 4 or 6), installed via --relayNode.
//   The relay uses the SAME groupcast L2ID (254) and same multicast group
//   (225.63.63.1) as the leader — intentionally. This creates a single shared
//   groupcast pool where relay NACKs trigger leader retransmissions and vice
//   versa: the "reTX storm" effect.
//
// Sweep parameters:
//   --speed  km/h (sets platoon velocity and IVD)
//   --ivd    inter-vehicle distance (m)
//   --mcr    min communication range for PSFCH feedback (m, HARQ Option-1 only)
//   --relayNode  which node is the relay (4 or 6, never both at once)
//
// KPIs:
//   Standard kpi_hooks + kpi_postprocess; per-hop reTX from psschTxUeMac.
//
// Orchestration (7 steps):
//   1. CLI + validation
//   2. InstallMobility (ConstantVelocityMobilityModel)
//   3. BuildSlStack (20 MHz / mu=2 / 10 subch / PSFCH-4 / HARQ Option-2 /
//      no MCR gate — spec-faithful: Zone-ID mechanism not modeled, all UEs
//      are group members in our single-dstL2Id setup so no gate = no filter)
//   4. InstallGroupcastFlows (node 0 leader, all platoon RX)
//   5. InstallSameL2IdRelay (relay at relayNode)
//   6. KpiHooks + trace connect
//   7. Simulator::Run() + Flush()

#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
#include "common/kpi/v2x-kpi.h"
#include "common/mobility/mobility.h"
#include "common/relay/same_l2id_relay_app.h"
#include "common/stack/nr_sl_stack.h"

#include "ns3/core-module.h"
#include "ns3/log.h"
#include "ns3/nr-sl-ue-mac.h"
#include "ns3/nr-ue-net-device.h"

#include <cmath>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("RelayStormEval");

int
main(int argc, char* argv[])
{
    // -----------------------------------------------------------------------
    // 1. CLI
    // -----------------------------------------------------------------------
    std::string runId        = "run0";
    uint32_t    seed         = 1;
    uint32_t    numUEs       = 9;      // 9-vehicle platoon

    // Platoon motion
    double      speedKmh     = 80.0;   // platoon speed (km/h)
    double      ivd          = 23.0;   // inter-vehicle distance (m)
    double      staticLaneY  = 0.0;
    double      staticFrontX = 1000.0;
    double      antennaHeight = 1.5;

    // Radio resource
    // mu=2 (60 kHz SCS): legacy 200<<2=800 units = 80 MHz → ~111 PRBs.
    // 10 subchannels × 10 PRBs = 100 PRBs — fits in 111 PRBs.
    // channelBwMHz=0 = legacy auto-derive (80 MHz at mu=2).
    uint32_t    mu           = 2;      // numerology 2 → 60 kHz SCS
    double      channelBwMHz = 0.0;    // 0 = legacy (200<<mu = 80 MHz at mu=2)
    uint32_t    numSubchannels = 10;   // 10 subchannels
    uint32_t    bwPRBs       = 10;    // 10 PRBs/subchannel (valid pool size)
    uint32_t    mcs          = 14;
    uint8_t     maxNumTx     = 5;
    uint16_t    psfchPeriod  = 4;      // PSFCH every 4 slots (Option 2)
    bool        harqEnabled  = true;   // Option 2 = ACK + NACK
    bool        shadowing    = true;

    // MCR (HARQ Option-1 only — Option-2 ignores per 3GPP TS 38.321)
    // Spec-faithful behavior: HARQ Option-2 (Groupcast) gates PSFCH feedback by
    // Zone-ID membership (sl-ZoneConfig), NOT by distance. Since we don't model
    // Zone-IDs and our single-dstL2Id deployment has all UEs as group members,
    // the spec-faithful Option-2 has no distance filter. BuildSlStack therefore
    // ignores mcrMeters when harqOption == 2.
    // Historical runs of this campaign used the now-deprecated MCD distance gate
    // applied unconditionally; numbers in current CSVs carry that bias.
    double      mcrMeters    = 150.0;  // (Option-1 only) feedback suppressed beyond MCR

    // Relay
    uint32_t    relayNode    = 4;      // 4 or 6 (single relay per run)
    bool        relayEnabled = true;

    // Traffic
    double      leaderPktBytes = 300.0; // 300 B safety messages
    double      leaderPdbMs    = 10.0;  // PDB = 10 ms
    double      leaderRriMs    = 10.0;  // RRI = 10 ms (periodic2 style)

    // Sim control
    double      simTime      = 10.0;
    double      warmup       = 1.0;
    std::string outputDir    = "./";
    std::string simTag;

    // PSFCH KPI (per-opportunity ACK/NACK/SILENCE)
    bool        psfchKpi          = false;
    bool        psfchKpiSummaryOnly = false;
    std::string psfchKpiPath;

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId",         "Unique run identifier",                           runId);
    cmd.AddValue("seed",          "Random seed",                                     seed);
    cmd.AddValue("numUEs",        "Number of UE nodes (default 9)",                  numUEs);
    cmd.AddValue("speed",         "Platoon speed (km/h)",                            speedKmh);
    cmd.AddValue("ivd",           "Inter-vehicle distance (m)",                      ivd);
    cmd.AddValue("laneY",         "Lane y-coordinate (m)",                           staticLaneY);
    cmd.AddValue("frontX",        "Initial x of lead vehicle (m)",                   staticFrontX);
    cmd.AddValue("antennaHeight", "Antenna height (m)",                              antennaHeight);
    cmd.AddValue("mu",            "Numerology (2=60kHz)",                            mu);
    cmd.AddValue("channelBwMHz",  "SLRP channel bandwidth (MHz)",                   channelBwMHz);
    cmd.AddValue("numSubchannels","Number of subchannels in resource pool",          numSubchannels);
    cmd.AddValue("bwPRBs",        "Subchannel size in PRBs (10/15/20/25/50/75/100)",bwPRBs);
    cmd.AddValue("mcs",           "Fixed MCS index",                                 mcs);
    cmd.AddValue("maxNumTx",      "Max PSSCH TX per TB (1..32)",                     maxNumTx);
    cmd.AddValue("psfchPeriod",   "PSFCH period in slots (4=Option-2)",              psfchPeriod);
    cmd.AddValue("harq",          "Enable HARQ (true=Option-2 ACK+NACK)",            harqEnabled);
    cmd.AddValue("shadowing",     "Enable shadowing",                                shadowing);
    cmd.AddValue("mcr",           "Min communication range for PSFCH gate (m, Option-1 only)",   mcrMeters);
    cmd.AddValue("relayNode",     "Relay node index (4 or 6; 0 = no relay)",        relayNode);
    cmd.AddValue("relayEnabled",  "Enable same-L2ID relay",                         relayEnabled);
    cmd.AddValue("pktBytes",      "Leader packet size (bytes)",                      leaderPktBytes);
    cmd.AddValue("pdb",           "Leader PDB (ms)",                                 leaderPdbMs);
    cmd.AddValue("rri",           "Leader RRI (ms)",                                 leaderRriMs);
    cmd.AddValue("simTime",       "Traffic duration (s)",                            simTime);
    cmd.AddValue("warmup",        "Warm-up seconds discarded by V2xKpi",             warmup);
    cmd.AddValue("outputDir",     "Output directory for KPI SQLite DB",              outputDir);
    cmd.AddValue("simTag",        "DB filename tag (default = runId)",               simTag);
    cmd.AddValue("psfchKpi",        "Record per-PSFCH-opportunity ACK/NACK/SILENCE", psfchKpi);
    cmd.AddValue("psfchKpiSummaryOnly", "Emit only summary CSV (no per-event rows)", psfchKpiSummaryOnly);
    cmd.AddValue("psfchKpiPath",     "Override PSFCH KPI CSV path (default = outputDir/simTag_psfch_opps.csv)", psfchKpiPath);
    cmd.Parse(argc, argv);

    if (simTag.empty()) simTag = runId;
    numUEs = std::max(numUEs, 2u);
    if (relayNode == 0) relayEnabled = false;
    if (relayEnabled && relayNode >= numUEs)
    {
        std::cerr << "relay_storm_eval: relayNode=" << relayNode
                  << " >= numUEs=" << numUEs << "\n";
        return 1;
    }
    if (psfchPeriod != 0 && psfchPeriod != 1 &&
        psfchPeriod != 2 && psfchPeriod != 4)
    {
        std::cerr << "relay_storm_eval: --psfchPeriod must be 0,1,2 or 4\n";
        return 1;
    }

    RngSeedManager::SetSeed(1);
    RngSeedManager::SetRun(seed);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    const double speedMps = speedKmh / 3.6;

    NS_LOG_INFO("=== relay_storm_eval ==="
                << " speed=" << speedKmh << " km/h"
                << " ivd=" << ivd << " m"
                << " mcr=" << mcrMeters << " m (Option-1 only)"
                << " relay=" << (relayEnabled ? std::to_string(relayNode) : "none")
                << " seed=" << seed);

    // -----------------------------------------------------------------------
    // 2. Moving platoon mobility
    // -----------------------------------------------------------------------
    MobConfig mobCfg;
    mobCfg.numUEs          = numUEs;
    mobCfg.seed            = seed;
    mobCfg.staticPlatoon   = true;
    mobCfg.ivd             = ivd;
    mobCfg.staticLaneY     = staticLaneY;
    mobCfg.staticFrontX    = staticFrontX;
    mobCfg.antennaHeight   = antennaHeight;
    mobCfg.platoonSpeedMps = speedMps; // > 0 → ConstantVelocityMobilityModel

    MobResult mob = InstallMobility(mobCfg);
    const NodeContainer& ueNodes = mob.ueNodes;

    // -----------------------------------------------------------------------
    // 3. NR-SL stack: 20 MHz / mu=2 / 10 subchannels / PSFCH-4 / no MCR gate
    //    (HARQ Option-2 — spec-faithful: no distance gate, Zone-ID mechanism
    //    not modeled but in single-dstL2Id deployments all UEs are members
    //    so the spec-faithful behavior is no gate at all.)
    // -----------------------------------------------------------------------
    GeomChannelCfg geom;
    // Static collinear platoon → no SUMO blocker IDs needed. The NLOSv model
    // will use intermediate vehicles as blockers automatically.
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
    sp.scenario         = BandwidthPartInfo::V2V_Highway;
    sp.adaptiveMcs      = false; // fixed MCS sweep
    sp.schedulerMode    = "sbsps";
    sp.harqOption       = 2;            // HARQ Option-2 (ACK/NACK groupcast)
    sp.mcrMeters        = 0.0;          // gate skipped for Option-2 per spec
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
    // Build RRI list: only the leader RRI (10 ms)
    sp.rriList          = {static_cast<uint16_t>(std::lround(leaderRriMs))};

    SlStackCtx ctx = BuildSlStack(sp, geom, ueNodes);

    // -----------------------------------------------------------------------
    // 4. Leader groupcast flow: node 0 TX → all nodes RX
    //    Same dstL2Id (254) and multicast group as existing common flows.
    //    TrafficClass::OnOffConstant with 300 B @ RRI=10 ms.
    // -----------------------------------------------------------------------
    // Build a list with the leader's followers as rxNodes (nodes 1..N-1).
    std::vector<uint32_t> allFollowers;
    for (uint32_t i = 1; i < numUEs; ++i) allFollowers.push_back(i);

    FlowDef leaderFlow;
    leaderFlow.label      = "leader_cam";
    leaderFlow.port       = 8001;
    leaderFlow.priority   = 1;
    leaderFlow.rri        = MilliSeconds(static_cast<uint32_t>(leaderRriMs));
    leaderFlow.dynamic    = false;  // SPS
    leaderFlow.pdb        = MilliSeconds(static_cast<uint32_t>(leaderPdbMs));
    leaderFlow.rateKbps   = (leaderPktBytes * 8.0) / (leaderRriMs / 1000.0) / 1000.0;
    leaderFlow.enabled    = true;
    leaderFlow.harqOverride = harqEnabled ? 1 : 0;
    leaderFlow.txNodes    = {0};        // only leader transmits
    leaderFlow.rxNodes    = allFollowers; // all followers receive
    leaderFlow.dstL2Id    = 254;
    leaderFlow.mcs        = -1;
    leaderFlow.trafficClass = TrafficClass::OnOffConstant;

    int64_t stream = ctx.stream;
    FlowResult flows = InstallGroupcastFlows(ueNodes, ctx,
                                             {leaderFlow},
                                             simTime,
                                             harqEnabled,
                                             stream);

    // -----------------------------------------------------------------------
    // 5. Same-L2ID relay
    // -----------------------------------------------------------------------
    Ptr<SameL2IdRelayApp> relayApp;
    if (relayEnabled)
    {
        relayApp = InstallSameL2IdRelay(ueNodes, ctx, flows,
                                        relayNode, 0 /*flowIdx*/, simTime);
        NS_LOG_INFO("Same-L2ID relay installed on node " << relayNode);
    }

    // -----------------------------------------------------------------------
    // 6. KPI hooks
    // -----------------------------------------------------------------------
    // KpiHooks constructor appends ".db" automatically — pass base path only.
    const std::string dbPath =
        outputDir + "/" + simTag;
    const std::string csvPath = ""; // no per-packet CSV for sweep runs

    KpiHooks kpi(dbPath, csvPath);
    // NOTE: do not override SQLite journal mode here — the default (DELETE)
    // is consistent with what other sims use and avoids WAL mode issues.
    // kpi.db.WaitExec("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;");

    ConnectPhyMacTraces(kpi);
    ConnectAppTraces(kpi, flows);

    // Wire relay timing to KPI.
    if (relayApp)
    {
        relayApp->SetTimingCb(
            [&kpi](uint32_t seq, uint16_t port, uint32_t nodeId,
                   double genSec, double decodeSec, double sendSec, int fwd) {
                kpi.WriteRelayTiming(seq, port, nodeId,
                                     genSec, decodeSec, sendSec, fwd);
            });
    }

    // Set V2xKpi DB path and TX duration. Range = 0 disables IP-position lookup
    // (no range gate at any layer for HARQ Option-2 — spec-faithful).
    kpi.v2xKpi.SetDbPath(dbPath);
    kpi.v2xKpi.SetTxAppDuration(simTime);
    kpi.v2xKpi.SetRangeForV2xKpis(0); // 0 = no range gate (no PSFCH gate for Option-2)

    // -----------------------------------------------------------------------
    // 7. Run
    // -----------------------------------------------------------------------
    NS_LOG_INFO("Starting simulation, simTime=" << simTime << " s");
    Simulator::Stop(Seconds(2.0 + simTime + 1.0));
    Simulator::Run();

    // Snapshot node positions BEFORE Destroy() clears the NodeList.
    PreFlush(kpi);

    Simulator::Destroy();

    Flush(kpi);

    // Write a one-line run summary CSV so the sweep script can aggregate.
    if (!outputDir.empty())
    {
        const std::string summaryPath = outputDir + "/" + simTag + "_summary.csv";
        std::ofstream sf(summaryPath);
        sf << "runId,seed,speedKmh,ivd,mcrMeters,relayNode,relayEnabled,mu,"
              "channelBwMHz,numSubchannels,mcs,psfchPeriod,simTime,harqOption,psfchKpi\n";
        sf << runId << "," << seed << "," << speedKmh << "," << ivd << ","
           << mcrMeters << "," << relayNode << ","
           << (relayEnabled ? "true" : "false") << ","
           << mu << "," << channelBwMHz << "," << numSubchannels << ","
           << mcs << "," << psfchPeriod << "," << simTime << "," << 2 << ","
           << (psfchKpi ? "true" : "false") << "\n";
    }

    NS_LOG_INFO("relay_storm_eval done. DB: " << dbPath << ".db");
    return 0;
}
