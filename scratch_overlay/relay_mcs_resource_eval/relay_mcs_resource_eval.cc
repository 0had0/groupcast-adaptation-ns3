// relay_mcs_resource_eval/relay_mcs_resource_eval.cc
//
// NR-V2X moving-platoon 8-vehicle MCS-vs-relay resource study.
//
// Topology:
//   Node 0 = leader (TX), nodes 1..7 = followers (RX), node R = single same-L2ID
//   relay.  Per-node MCS: leader at mcsHop1, relay at mcsHop2 (both pinned via
//   SlStackParams::nodeMcsOverride{} so each TX node uses a fresh
//   NrSlUeMacSchedulerFixedMcs at its own MCS).  Same L2ID (254) for both hops
//   -- the storm is intentional.
//
// --mode=baseline : only the leader TXs at the global MCS (=mcsBaseline).
// --mode=relay    : leader TXs at mcsHop1, relay at mcsHop2 (nodeMcsOverride).
//
// Sweep parameters:
//   --speed    km/h (sets platoon velocity and IVD)
//   --ivd      inter-vehicle distance (m)
//   --mcd      maximum communication distance for PSFCH feedback (m)
//   --relayNode which node is the relay (2..6, never both at once)
//   --mcsHop1  leader MCS (relay mode) or global MCS (baseline mode)
//   --mcsHop2  relay MCS (relay mode, ignored in baseline mode)
//
// KPIs:
//   Standard kpi_hooks + kpi_postprocess (now with absolute subchannel-slot
//   counters); per-hop reTX from psschTxUeMac; per-packet relay timing.
//
// Orchestration (7 steps):
//   1. CLI + validation
//   2. InstallMobility (static collinear platoon with ConstantVelocity)
//   3. BuildSlStack (20 MHz / mu=2 / 10 subch / PSFCH-4 / MCD gate / nodeMcsOverride)
//   4. InstallGroupcastFlows (node 0 leader, all platoon RX)
//   5. InstallSameL2IdRelay (relay at relayNode, in --mode=relay only)
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

NS_LOG_COMPONENT_DEFINE("RelayMcsResourceEval");

int
main(int argc, char* argv[])
{
    // -----------------------------------------------------------------------
    // 1. CLI
    // -----------------------------------------------------------------------
    std::string runId        = "run0";
    uint32_t    seed         = 1;
    uint32_t    numUEs       = 8;      // 8-vehicle platoon

    // Mode
    std::string mode         = "relay"; // "baseline" | "relay"

    // Platoon motion
    double      speedKmh     = 100.0;
    double      ivd          = 29.0;
    double      staticLaneY  = 0.0;
    double      staticFrontX = 1000.0;
    double      antennaHeight = 1.5;

    // Radio resource
    uint32_t    mu           = 2;       // numerology 2 -> 60 kHz SCS
    double      channelBwMHz = 0.0;     // 0 = legacy auto-derive (80 MHz at mu=2)
    uint32_t    numSubchannels = 10;    // 10 subchannels
    uint32_t    bwPRBs       = 10;     // 10 PRBs/subchannel
    uint32_t    mcsBaseline  = 6;       // baseline MCS (and mcsHop1 in relay mode default)
    uint32_t    mcsHop2      = 14;      // relay MCS in relay mode
    uint8_t     maxNumTx     = 5;
    uint16_t    psfchPeriod  = 4;       // PSFCH every 4 slots (Option 2)
    bool        harqEnabled  = true;    // Option 2 = ACK + NACK
    bool        shadowing    = true;

    // MCD
    double      mcdMeters    = 100.0;   // feedback suppressed beyond MCD

    // Relay
    uint32_t    relayNode    = 4;       // 2..6 (single relay per run)

    // Traffic
    double      leaderPktBytes = 300.0; // 300 B safety messages
    double      leaderPdbMs    = 10.0;  // PDB = 10 ms
    double      leaderRriMs    = 10.0;  // RRI = 10 ms (periodic2 style)

    // Sim control
    double      simTime      = 5.0;
    double      warmup       = 1.0;
    std::string outputDir    = "./";
    std::string simTag;

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId",         "Unique run identifier",                           runId);
    cmd.AddValue("seed",          "Random seed",                                     seed);
    cmd.AddValue("numUEs",        "Number of UE nodes (default 8)",                  numUEs);
    cmd.AddValue("mode",          "Mode: 'baseline' (single-hop) or 'relay'",       mode);
    cmd.AddValue("speed",         "Platoon speed (km/h)",                            speedKmh);
    cmd.AddValue("ivd",           "Inter-vehicle distance (m)",                      ivd);
    cmd.AddValue("laneY",         "Lane y-coordinate (m)",                           staticLaneY);
    cmd.AddValue("frontX",        "Initial x of lead vehicle (m)",                   staticFrontX);
    cmd.AddValue("antennaHeight", "Antenna height (m)",                             antennaHeight);
    cmd.AddValue("mu",            "Numerology (2=60kHz)",                            mu);
    cmd.AddValue("channelBwMHz",  "SLRP channel bandwidth (MHz)",                   channelBwMHz);
    cmd.AddValue("numSubchannels","Number of subchannels in resource pool",          numSubchannels);
    cmd.AddValue("bwPRBs",        "Subchannel size in PRBs (10/15/20/25/50/75/100)",bwPRBs);
    cmd.AddValue("mcsBaseline",   "Baseline MCS (= mcsHop1 in relay mode)",         mcsBaseline);
    cmd.AddValue("mcsHop2",       "Relay MCS (relay mode only)",                     mcsHop2);
    cmd.AddValue("maxNumTx",      "Max PSSCH TX per TB (1..32)",                     maxNumTx);
    cmd.AddValue("psfchPeriod",   "PSFCH period in slots (4=Option-2)",              psfchPeriod);
    cmd.AddValue("harq",          "Enable HARQ (true=Option-2 ACK+NACK)",            harqEnabled);
    cmd.AddValue("shadowing",     "Enable shadowing",                                shadowing);
    cmd.AddValue("mcd",           "Max communication distance for PSFCH gate (m)",   mcdMeters);
    cmd.AddValue("relayNode",     "Relay node index (2..6; 0 = force baseline mode)",relayNode);
    cmd.AddValue("pktBytes",      "Leader packet size (bytes)",                      leaderPktBytes);
    cmd.AddValue("pdb",           "Leader PDB (ms)",                                 leaderPdbMs);
    cmd.AddValue("rri",           "Leader RRI (ms)",                                 leaderRriMs);
    cmd.AddValue("simTime",       "Traffic duration (s)",                            simTime);
    cmd.AddValue("warmup",        "Warm-up seconds discarded by V2xKpi",             warmup);
    cmd.AddValue("outputDir",     "Output directory for KPI SQLite DB",              outputDir);
    cmd.AddValue("simTag",        "DB filename tag (default = runId)",               simTag);
    cmd.Parse(argc, argv);

    if (simTag.empty()) simTag = runId;
    numUEs = std::max(numUEs, 2u);

    if (mode != "baseline" && mode != "relay")
    {
        std::cerr << "relay_mcs_resource_eval: --mode must be 'baseline' or 'relay'\n";
        return 1;
    }
    if (mode == "relay" && (relayNode < 1 || relayNode >= numUEs))
    {
        std::cerr << "relay_mcs_resource_eval: --relayNode=" << relayNode
                  << " must be in [1, " << (numUEs - 1) << "] for mode=relay\n";
        return 1;
    }
    if (psfchPeriod != 0 && psfchPeriod != 1 &&
        psfchPeriod != 2 && psfchPeriod != 4)
    {
        std::cerr << "relay_mcs_resource_eval: --psfchPeriod must be 0,1,2 or 4\n";
        return 1;
    }

    RngSeedManager::SetSeed(1);
    RngSeedManager::SetRun(seed);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    const double speedMps = speedKmh / 3.6;

    NS_LOG_INFO("=== relay_mcs_resource_eval ==="
                << " mode=" << mode
                << " speed=" << speedKmh << " km/h"
                << " ivd=" << ivd << " m"
                << " mcd=" << mcdMeters << " m"
                << " relay=" << (mode == "relay" ? std::to_string(relayNode) : "none")
                << " mcsBaseline(hop1)=" << mcsBaseline
                << " mcsHop2=" << mcsHop2
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
    mobCfg.platoonSpeedMps = speedMps; // > 0 -> ConstantVelocityMobilityModel

    MobResult mob = InstallMobility(mobCfg);
    const NodeContainer& ueNodes = mob.ueNodes;

    // -----------------------------------------------------------------------
    // 3. NR-SL stack: 20 MHz / mu=2 / 10 subchannels / PSFCH-4 / MCD gate
    // -----------------------------------------------------------------------
    GeomChannelCfg geom;
    geom.blockerLengthM = 4.5;
    geom.blockerWidthM  = 2.0;

    SlStackParams sp;
    sp.mu               = mu;
    sp.bwPRBs           = bwPRBs;
    sp.channelBwMHz     = channelBwMHz;
    sp.numSubchannels   = numSubchannels;
    // Global MCS = mcsBaseline (used by RX-only nodes; in baseline mode it is
    // also the leader MCS; in relay mode node 0's scheduler is replaced by
    // nodeMcsOverride to pin the leader at mcsHop1 = mcsBaseline).
    sp.mcs              = static_cast<uint8_t>(mcsBaseline);
    sp.seed             = seed;
    sp.maxNumTx         = static_cast<uint8_t>(maxNumTx);
    sp.psfchPeriod      = psfchPeriod;
    sp.shadowingEnabled = shadowing;
    sp.scenario         = BandwidthPartInfo::V2V_Highway;
    sp.adaptiveMcs      = false; // fixed-MCS sweep
    sp.schedulerMode    = "sbsps";
    sp.mcdMeters        = mcdMeters;
    sp.rriList          = {static_cast<uint16_t>(std::lround(leaderRriMs))};

    // Per-node MCS override: in relay mode, pin the relay node to mcsHop2
    // (leader stays at the global mcs = mcsBaseline = mcsHop1).
    if (mode == "relay")
    {
        sp.nodeMcsOverride[relayNode] = static_cast<uint8_t>(mcsHop2);
    }

    SlStackCtx ctx = BuildSlStack(sp, geom, ueNodes);

    // -----------------------------------------------------------------------
    // 4. Leader groupcast flow: node 0 TX -> all nodes RX
    // -----------------------------------------------------------------------
    std::vector<uint32_t> allFollowers;
    for (uint32_t i = 1; i < numUEs; ++i) allFollowers.push_back(i);

    FlowDef leaderFlow;
    leaderFlow.label      = "leader_cam";
    leaderFlow.port       = 8001;
    leaderFlow.priority   = 1;
    leaderFlow.rri        = MilliSeconds(static_cast<uint32_t>(leaderRriMs));
    leaderFlow.dynamic    = false;
    leaderFlow.pdb        = MilliSeconds(static_cast<uint32_t>(leaderPdbMs));
    leaderFlow.rateKbps   = (leaderPktBytes * 8.0) / (leaderRriMs / 1000.0) / 1000.0;
    leaderFlow.enabled    = true;
    leaderFlow.harqOverride = harqEnabled ? 1 : 0;
    leaderFlow.txNodes    = {0};
    leaderFlow.rxNodes    = allFollowers;
    leaderFlow.dstL2Id    = 254;
    leaderFlow.mcs        = -1; // MCS is set by the scheduler (mcsBaseline / mcsHop2)
    leaderFlow.trafficClass = TrafficClass::OnOffConstant;

    int64_t stream = ctx.stream;
    FlowResult flows = InstallGroupcastFlows(ueNodes, ctx,
                                             {leaderFlow},
                                             simTime,
                                             harqEnabled,
                                             stream);

    // -----------------------------------------------------------------------
    // 5. Same-L2ID relay (relay mode only)
    // -----------------------------------------------------------------------
    Ptr<SameL2IdRelayApp> relayApp;
    if (mode == "relay")
    {
        relayApp = InstallSameL2IdRelay(ueNodes, ctx, flows,
                                        relayNode, 0 /*flowIdx*/, simTime);
        NS_LOG_INFO("Same-L2ID relay installed on node " << relayNode
                    << " with per-node MCS " << mcsHop2);
    }

    // -----------------------------------------------------------------------
    // 6. KPI hooks
    // -----------------------------------------------------------------------
    const std::string dbPath  = outputDir + "/" + simTag;
    const std::string csvPath = ""; // no per-packet CSV for sweep runs

    KpiHooks kpi(dbPath, csvPath);

    ConnectPhyMacTraces(kpi);
    ConnectAppTraces(kpi, flows);

    // Wire relay timing to KPI (only present in relay mode).
    if (relayApp)
    {
        relayApp->SetTimingCb(
            [&kpi](uint32_t seq, uint16_t port, uint32_t nodeId,
                   double genSec, double decodeSec, double sendSec, int fwd) {
                kpi.WriteRelayTiming(seq, port, nodeId,
                                     genSec, decodeSec, sendSec, fwd);
            });
    }

    kpi.v2xKpi.SetDbPath(dbPath);
    kpi.v2xKpi.SetTxAppDuration(simTime);
    kpi.v2xKpi.SetRangeForV2xKpis(0); // PSFCH gate handles MCD

    // -----------------------------------------------------------------------
    // 7. Run
    // -----------------------------------------------------------------------
    NS_LOG_INFO("Starting simulation, simTime=" << simTime << " s");
    Simulator::Stop(Seconds(2.0 + simTime + 1.0));
    Simulator::Run();

    PreFlush(kpi);

    Simulator::Destroy();

    Flush(kpi);

    // One-line run summary CSV so the sweep script can aggregate.
    if (!outputDir.empty())
    {
        const std::string summaryPath = outputDir + "/" + simTag + "_summary.csv";
        std::ofstream sf(summaryPath);
        sf << "runId,seed,mode,speedKmh,ivd,mcdMeters,relayNode,mcsBaseline,"
              "mcsHop1,mcsHop2,mu,channelBwMHz,numSubchannels,bwPRBs,"
              "psfchPeriod,maxNumTx,simTime\n";
        sf << runId << "," << seed << "," << mode << ","
           << speedKmh << "," << ivd << "," << mcdMeters << ","
           << (mode == "relay" ? std::to_string(relayNode) : "0") << ","
           << mcsBaseline << "," << mcsBaseline << "," << mcsHop2 << ","
           << mu << "," << channelBwMHz << "," << numSubchannels << ","
           << bwPRBs << "," << psfchPeriod << ","
           << static_cast<uint32_t>(maxNumTx) << "," << simTime << "\n";
    }

    NS_LOG_INFO("relay_mcs_resource_eval done. DB: " << dbPath << ".db");
    return 0;
}
