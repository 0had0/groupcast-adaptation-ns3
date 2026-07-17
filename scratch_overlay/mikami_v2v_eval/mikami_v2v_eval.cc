// mikami_v2v_eval.cc
//
// Reproduction of Mikami et al. 2020 ("Field Experimental Evaluation on Latency
// and Reliability Performance of 5G NR V2V Direct Communication in Real Express
// Highway Environment", IEEE SoftBank).
//
// Goal: validate that the ns-3.42 + CTTC NR + scratch_overlay simulator
// reproduces the qualitative KPIs reported in the paper for Sidelink Mode-2
// (Out-of-Coverage) at 5.9 GHz / 60 kHz SCS / 20 MHz with HARQ (max 2 ReTx).
//
// Experiment parameters (from TABLE I + TABLE II of the paper):
//
//   Carrier frequency     5.89 GHz (3GPP n47 SL band; paper's 4.5 GHz BS
//                         band has no V2X counterpart in ns-3.42)
//   Bandwidth             20 MHz
//   SCS / µ               60 kHz (mu=2)
//   TTI / subframe        0.25 ms
//   Tx/Rx antennas        2T/2R (model default; paper 8T/8R for hardware)
//   Polarization          isotropic (paper uses ±45°; out of ns-3 scope)
//   UE antenna height     3.8 m
//   Topology              3 trucks: UE#1 (leader) | UE#2 | UE#3
//   Inter-vehicle dist    SL#1 = 47 m (UE#1↔UE#2), SL#2 = 94 m (UE#1↔UE#3)
//   Vehicle speed         80 km/h (22.222 m/s, +x direction)
//   Packet payload        32 B / packet
//   Packet interval       10 ms (100 pkt/s per UE)
//   HARQ                  On, max 2 ReTx  (maxNumTx = 3)
//   SL mode               Mode-2 (Out-of-Coverage) — no gNB
//   Channel scenario      V2V_Highway
//
// KPIs collected (mirrors Figs. 8-13 of the paper):
//   1. Over-the-air OTT latency (UE#1↔UE#2 / UE#1↔UE#3, both directions)
//   2. Initial PSSCH BLER (1-second averaged over the run)
//   3. SL-SINR per RX link (averaged over the run)
//   4. PRR before HARQ retransmission (PRR achieved on first TX attempt)
//   5. PRR after HARQ retransmission (PRR achieved after up to 3 TX attempts)
//
// Architecture follows the 7-step pattern documented in
// scratch_overlay/groupcast_eval/RUNNING.md.

#include "common/channel/geometric_channel.h"
#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
#include "common/mobility/mobility.h"
#include "common/stack/nr_sl_stack.h"

#include "ns3/core-module.h"
#include "ns3/log.h"
#include "ns3/nr-sl-ue-mac.h"
#include "ns3/nr-ue-net-device.h"

#include <algorithm>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("MikamiV2VEval");

// ---------------------------------------------------------------------------
// CLI defaults map directly to paper TABLE I/II.
// ---------------------------------------------------------------------------
int
main(int argc, char* argv[])
{
    // -- Run identity --
    std::string runId        = "mikami_default";
    uint32_t    seed         = 1;

    // -- Topology (paper TABLE II) --
    uint32_t    numUEs       = 3;          // UE#1 (leader), UE#2, UE#3
    double      ivd          = 47.0;       // inter-vehicle distance [m]; 47 → UE#2, 94 → UE#3
    double      speedKmph    = 80.0;       // 80 km/h target
    double      antennaHeight = 3.8;       // TABLE II

    // -- PHY/MAC (paper TABLE I) --
    uint32_t    mu           = 2;          // 60 kHz SCS
    double      channelBwMHz = 20.0;       // 20 MHz BW
    uint32_t    bwPRBs       = 10;         // subchannel size: 27 PRBs / 10 = 2 subchannels
    uint32_t    mcs          = 14;         // mid-MCS (common starting point)
    uint32_t    maxNumTx     = 3;          // 1 initial + 2 ReTx → maxNumTx=3 (TABLE II)
    uint16_t    psfchPeriod  = 1;          // HARQ feedback every slot

    // -- Traffic (paper TABLE II: 32 B / 10 ms) --
    uint32_t    pktSizeBytes = 32;
    uint32_t    pktIntervalMs = 10;        // 100 packets/s
    uint32_t    port         = 8001;

    // -- Timing --
    double      simTime      = 20.0;       // seconds of application traffic per flow
    double      warmup       = 2.0;        // bearerActivation = 2 s

    // -- Channel --
    std::string baseScenarioStr = "V2V_Highway";
    bool        shadowing    = true;
    double      blockerL     = 12.0;       // paper: vehicle length = 12 m
    double      blockerW     = 2.5;        // paper: large-size truck width ≈ 2.5 m

    // -- HARQ --
    bool        harqEnabled  = true;

    // -- Output --
    std::string outputDir    = "./";
    std::string simTag;
    std::string outRunCsv;
    std::string outPacketsCsv;
    std::string outKpiDir;                 // post-processed KPIs (computed by postprocess script)

    CommandLine cmd(__FILE__);
    cmd.AddValue("runId",          "Unique run identifier",                         runId);
    cmd.AddValue("seed",           "RNG seed",                                       seed);
    cmd.AddValue("numUEs",         "Number of UEs (paper uses 3)",                   numUEs);
    cmd.AddValue("ivd",            "Inter-vehicle distance between adjacent trucks (m)", ivd);
    cmd.AddValue("speedKmph",      "Target vehicle speed (km/h)",                    speedKmph);
    cmd.AddValue("antennaHeight",  "UE antenna height from ground (m)",             antennaHeight);
    cmd.AddValue("mu",             "Numerology: 0=15kHz|1=30kHz|2=60kHz",            mu);
    cmd.AddValue("channelBwMHz",   "Channel bandwidth (MHz)",                        channelBwMHz);
    cmd.AddValue("bwPRBs",         "Subchannel size in PRBs",                        bwPRBs);
    cmd.AddValue("mcs",            "Fixed MCS index",                                mcs);
    cmd.AddValue("maxNumTx",       "Max PSSCH TX per TB (1=no retx; paper=3)",      maxNumTx);
    cmd.AddValue("psfchPeriod",    "PSFCH period (0=off, 1/2/4=on); HARQ feedback only flows when >0", psfchPeriod);
    cmd.AddValue("pktSizeBytes",   "Application-layer payload size (B)",             pktSizeBytes);
    cmd.AddValue("pktIntervalMs",  "Application-layer packet interval (ms)",        pktIntervalMs);
    cmd.AddValue("port",           "UDP destination port",                           port);
    cmd.AddValue("simTime",        "Traffic duration per flow (s)",                  simTime);
    cmd.AddValue("warmup",         "Warm-up seconds discarded by KPI",               warmup);
    cmd.AddValue("harq",           "Enable HARQ (paper: on)",                        harqEnabled);
    cmd.AddValue("baseScenario",   "3GPP base scenario (V2V_Highway | V2V_Urban)",  baseScenarioStr);
    cmd.AddValue("shadowing",      "Enable 3GPP shadowing",                          shadowing);
    cmd.AddValue("blockerL",       "Blocker vehicle length (m)",                     blockerL);
    cmd.AddValue("blockerW",       "Blocker vehicle width (m)",                      blockerW);
    cmd.AddValue("outputDir",      "Directory for KPI SQLite DB",                    outputDir);
    cmd.AddValue("simTag",         "DB filename tag (default = runId)",              simTag);
    cmd.AddValue("outRunCsv",      "One-line per-run summary CSV path",              outRunCsv);
    cmd.AddValue("outPacketsCsv",  "Per-packet CSV path",                            outPacketsCsv);
    cmd.AddValue("outKpiDir",      "Directory for post-processed KPI CSVs",          outKpiDir);
    cmd.Parse(argc, argv);

    // Validate
    if (numUEs < 3)
    {
        std::cerr << "mikami_v2v_eval: --numUEs must be >= 3 (paper uses 3 trucks)\n";
        return 1;
    }
    if (maxNumTx < 1 || maxNumTx > 32)
    {
        std::cerr << "mikami_v2v_eval: --maxNumTx must be in [1, 32]\n";
        return 1;
    }
    if (psfchPeriod != 0 && psfchPeriod != 1 && psfchPeriod != 2 && psfchPeriod != 4)
    {
        std::cerr << "mikami_v2v_eval: --psfchPeriod must be 0, 1, 2, or 4\n";
        return 1;
    }
    BandwidthPartInfo::Scenario baseScenario;
    if (baseScenarioStr == "V2V_Highway")     baseScenario = BandwidthPartInfo::V2V_Highway;
    else if (baseScenarioStr == "V2V_Urban")  baseScenario = BandwidthPartInfo::V2V_Urban;
    else if (baseScenarioStr == "UMi_StreetCanyon") baseScenario = BandwidthPartInfo::UMi_StreetCanyon;
    else if (baseScenarioStr == "UMa")        baseScenario = BandwidthPartInfo::UMa;
    else
    {
        std::cerr << "mikami_v2v_eval: unknown --baseScenario: " << baseScenarioStr << "\n";
        return 1;
    }
    if (simTag.empty()) simTag = runId;

    // ------------------------------------------------------------------------
    // 1. RNG + defaults
    //
    // Note: do NOT hardcode SetSeed(1) — kpi_hooks writes
    // RngSeedManager::GetSeed() / GetRun() into the SQLite SEED/RUN columns
    // and DataCache filters pktTxRx/psschRxUePhy by them, so the DB-side
    // SEED value must equal the requested --seed.
    // ------------------------------------------------------------------------
    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(1);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    // ------------------------------------------------------------------------
    // 2. Mobility — moving platoon at 80 km/h (paper TABLE II)
    //
    //    UE#1 (leader) at x = 1000; UE#2 at 1000 - 47 = 953; UE#3 at 1000 - 94 = 906
    //    All share y=0 (single-lane highway) and z=antennaHeight (3.8 m).
    //    ConstantVelocityMobilityModel gives real Doppler at the 60 kHz SCS.
    // ------------------------------------------------------------------------
    const double speedMps = speedKmph / 3.6;
    MobConfig mobCfg;
    mobCfg.numUEs         = numUEs;
    mobCfg.seed           = seed;
    mobCfg.staticPlatoon  = true;
    mobCfg.ivd            = ivd;
    mobCfg.staticFrontX   = 1000.0;
    mobCfg.staticLaneY    = 0.0;
    mobCfg.antennaHeight  = antennaHeight;
    mobCfg.platoonSpeedMps = speedMps;

    MobResult mob = InstallMobility(mobCfg);
    NodeContainer& ueNodes = mob.ueNodes;

    // ------------------------------------------------------------------------
    // 3. NR + Sidelink stack (paper TABLE I: 60 kHz, 20 MHz, HARQ, Polar, Mode-2)
    //
    //    Mode-2 in ns-3 means "no gNB" — the UE preconfigures sidelink resources
    //    via LteRrcSap::SidelinkPreconfigNr (no SIB from any cell).  This is
    //    exactly the Out-of-Coverage deployment described in the paper.
    // ------------------------------------------------------------------------
    SlStackParams stackParams;
    stackParams.mu               = mu;
    stackParams.channelBwMHz     = channelBwMHz;
    stackParams.bwPRBs           = bwPRBs;
    stackParams.mcs              = mcs;
    stackParams.seed             = seed;
    stackParams.maxNumTx         = static_cast<uint8_t>(maxNumTx);
    stackParams.psfchPeriod      = psfchPeriod;
    stackParams.shadowingEnabled = shadowing;
    stackParams.scenario         = baseScenario;
    stackParams.adaptiveMcs      = false;
    // HARQ Option-1 (NACK-only) is the spec-faithful MCR-gated feedback used by
    // groupcast in the paper's prototype. PSFCH disabled → pssch_rx table is
    // still populated with the per-TB outcome (m_corrupt / m_sinr) but no ACK
    // counter is delivered — so the sender always retransmits up to maxNumTx.
    // We KEEP psfchPeriod >= 1 here to model real HARQ with feedback, matching
    // the paper's "HARQ retransmission scheme" (Fig. 12(b) vs 13(b)).
    stackParams.harqOption       = 1;
    stackParams.mcrMeters        = 0.0;       // no distance gate; single-dstL2Id group

    // Geometry-aware NLOSv channel: in moving-platoon mode, all UE nodes are
    // treated as potential blockers for the LOS path between any pair straddling
    // them (UE#2 blocks UE#1↔UE#3 LOS).
    GeomChannelCfg geomCfg;
    geomCfg.blockerLengthM = blockerL;
    geomCfg.blockerWidthM  = blockerW;
    geomCfg.blockerNodes   = ueNodes;

    SlStackCtx stack = BuildSlStack(stackParams, geomCfg, ueNodes);

    // ------------------------------------------------------------------------
    // 4. Traffic — paper's "32 B / 10 ms" measurement packet, all 3 UEs both
    //    transmit and receive (mirrors Fig. 8(a)/(b) and 9(a)/(b) which measure
    //    both UE#1→{UE#2,UE#3} and {UE#2,UE#3}→UE#1 directions).
    //
    //    Rate = 32 * 8 / 0.01 = 25 600 bps = 25.6 kb/s per UE (matches paper).
    //    RRI = 10 ms — must be advertised in the pool's allowed RRI list.
    //    HARQ = groupcast (castType=Groupcast, harqEnabled=true).
    // ------------------------------------------------------------------------
    const double rateKbps = static_cast<double>(pktSizeBytes) * 8.0
                            / static_cast<double>(pktIntervalMs) * 1000.0 / 1000.0;

    std::ostringstream flowsSpec;
    flowsSpec << "label=veh_ctrl_msg"
              << ";port="    << port
              << ";prio=1"
              << ";rri_ms=" << pktIntervalMs
              << ";pdb_ms=" << pktIntervalMs
              << ";rate_kbps=" << rateKbps
              << ";harq=1"
              << ";dynamic=0"
              << ";tx_nodes=";       // empty = every UE transmits (full mesh)
    for (uint32_t i = 0; i < numUEs; ++i)
        flowsSpec << (i > 0 ? ":" : "") << i;
    flowsSpec << ";rxNodes=";
    for (uint32_t i = 0; i < numUEs; ++i)
        flowsSpec << (i > 0 ? ":" : "") << i;
    flowsSpec << ";dstL2Id=254";

    NS_LOG_INFO("Mikami flows spec: " << flowsSpec.str());

    std::vector<FlowDef> flowDefs;
    try
    {
        flowDefs = ParseFlowsSpec(flowsSpec.str());
    }
    catch (const std::exception& e)
    {
        std::cerr << "mikami_v2v_eval: --flows parse error: " << e.what() << "\n";
        return 1;
    }

    FlowResult flows = InstallGroupcastFlows(ueNodes, stack, flowDefs,
                                             simTime, harqEnabled, stack.stream);

    // ------------------------------------------------------------------------
    // 5. KPI hooks (SQLite DB + per-packet CSV)
    // ------------------------------------------------------------------------
    const std::string dbBase = outputDir + simTag;
    KpiHooks hooks(dbBase, outPacketsCsv);

    double txAppDuration = 0.0;
    for (size_t f = 0; f < flows.defs.size(); ++f)
        txAppDuration = std::max(txAppDuration,
                                 flows.appStop[f] - flows.appStart[f]);

    hooks.v2xKpi.SetDbPath(dbBase);
    hooks.v2xKpi.SetTxAppDuration(txAppDuration);
    hooks.v2xKpi.SetRangeForV2xKpis(1000);   // 1-km range gate

    ConnectPhyMacTraces(hooks);
    ConnectAppTraces(hooks, flows);

    // ------------------------------------------------------------------------
    // 6. Run
    // ------------------------------------------------------------------------
    double simStop = 0.0;
    for (size_t f = 0; f < flows.defs.size(); ++f)
        simStop = std::max(simStop, flows.appStart[f] + simTime + 0.5);
    NS_LOG_INFO("Mikami: starting Simulator for " << simStop << " s "
                << "(warmup=" << warmup << ", simTime=" << simTime
                << ", numUEs=" << numUEs << ", speed=" << speedKmph << " km/h)");
    Simulator::Stop(Seconds(simStop));
    Simulator::Run();

    // ------------------------------------------------------------------------
    // 7. Flush + run-summary CSV
    // ------------------------------------------------------------------------
    Flush(hooks);

    if (!outRunCsv.empty())
    {
        std::ofstream r(outRunCsv);
        if (r.is_open())
        {
            r << "runId,seed,numUEs,ivd,speedKmph,mu,channelBwMHz,bwPRBs,mcs,"
              << "maxNumTx,psfchPeriod,harq,pktSizeBytes,pktIntervalMs,"
              << "baseScenario,shadowing,simTime,warmup,kpiDb\n"
              << runId << ',' << seed << ',' << numUEs << ',' << ivd << ','
              << speedKmph << ',' << mu << ',' << channelBwMHz << ',' << bwPRBs
              << ',' << mcs << ',' << maxNumTx << ',' << psfchPeriod << ','
              << (harqEnabled ? 1 : 0) << ',' << pktSizeBytes << ','
              << pktIntervalMs << ',' << baseScenarioStr << ','
              << (shadowing ? 1 : 0) << ',' << simTime << ',' << warmup << ','
              << dbBase << ".db\n";
        }
    }

    Simulator::Destroy();
    NS_LOG_INFO("Mikami: simulation done. DB=" << dbBase << ".db");
    return 0;
}