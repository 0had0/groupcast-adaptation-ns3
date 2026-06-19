// sl_bler_sweep.cc
//
// Single NR 5G Sidelink (PC5) groupcast link: one Tx vehicle -> one Rx vehicle
// on a straight road (no SUMO; constant-velocity mobility). Measures PSSCH
// transport-block error rate (BLER) and the realised SNR for one parameter
// point. A bash driver (run_sweep.sh) sweeps MCS / distance / channel condition
// / speed / HARQ and aggregates the per-run CSV rows.
//
// NR-SL Mode 2, fully autonomous SB-SPS, no gNB. Fixed MCS (AMC disabled).
// Channel conditions: LOS and NLOS forced deterministically; NLOSv via the
// geometric channel-condition model with one fixed blocker placed mid-path.
// The 3GPP V2V model adds one log-normal blockage term (TR 37.885 §6.4.1),
// which is correct: N does not scale that additional loss.
// Shadowing + fast fading enabled; realtime (per-slot) channel updates.
//
// Reuses (compiled via relative paths in CMakeLists, never edited):
//   relay_groupcast_eval/{geometric_channel,three-gpp-geometric-nlosv,groupcast_flows}.cc
//   custom-traffic/custom-traffic-{application,helper}.cc
//
// All new code lives in scratch_overlay/sl_bler_sweep/.

#include "sl_stack_bler.h"

#include "common/flows/groupcast_flows.h"

#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <string>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("SlBlerSweep");

// ---------------------------------------------------------------------------
// BLER + KPI accumulators — HARQ-correct counting.
//
// total_tbs   : logical TBs transmitted. Counted at TX MAC SlPsschScheduling
//               trace, but only when ndi==1 (new data indicator set), so each
//               logical TB is counted exactly once regardless of retransmissions.
//               Robust denominator even on dead links (~100/s x simTime).
//
// rx_success  : logical TBs ultimately decoded. Counted at RX RxPsschTraceUe
//               as the number of events with m_corrupt==false. With HARQ
//               combining, a TB that succeeds on its k-th retransmission fires
//               m_corrupt=true for attempts 1..k-1 and m_corrupt=false for
//               attempt k — so exactly one m_corrupt=false event per
//               successfully decoded logical TB.
//
// failed_tbs  : total_tbs - rx_success  (lost control + all retransmissions
//               exhausted without decode success)
// bler        : failed_tbs / total_tbs  — per-logical-TB error rate
//
// mean_sinr   : averaged over first-attempt decode events (m_ndi==1 at RX),
//               which reflect the raw link SINR before HARQ combining.
//
// prr         : rx_success / total_tbs  — same as (1 - BLER) for single receiver
// pir_sec     : mean inter-reception time in seconds (from successful RX times)
// used_subch_slots : accumulated subchannels × slots used across all TX grants
// ---------------------------------------------------------------------------
struct BlerCounter
{
    uint64_t txTbs = 0;                    // logical TBs sent (ndi=1 events at TX MAC)
    uint64_t rxSuccess = 0;                // logical TBs decoded (m_corrupt=false at RX, any RV)
    double sumSinrLin = 0.0;               // linear SINR sum from first-attempt RX events
    uint64_t sinrCount = 0;
    std::vector<double> rxSuccessTimes;    // timestamps (s) of successful RX events for PIR
    double usedSubchSlots = 0.0;           // accumulated (subchannels × slots) used in TX grants
};

static void
OnPsschTx(BlerCounter* c, const SlPsschUeMacStatParameters p)
{
    if (p.ndi == 1)
    {
        c->txTbs++;
        // Accumulate subchannels × slots: rbLength / subChannelSize gives subchannels per grant
        if (p.subChannelSize > 0)
        {
            double subchUsed = static_cast<double>(p.rbLength) / static_cast<double>(p.subChannelSize);
            c->usedSubchSlots += subchUsed;
        }
    }
}

static void
OnPsschRx(BlerCounter* c, const SlRxDataPacketTraceParams p)
{
    if (!p.m_corrupt)
    {
        c->rxSuccess++;
        // Record RX timestamp for PIR calculation (in seconds)
        c->rxSuccessTimes.push_back(Simulator::Now().GetSeconds());
    }

    // SINR from first-attempt events only (pre-HARQ-combining, raw link quality)
    if (p.m_ndi == 1 && p.m_sinr > 0.0)
    {
        c->sumSinrLin += p.m_sinr;
        c->sinrCount++;
    }
}

// ---------------------------------------------------------------------------
static BandwidthPartInfo::Scenario
ParseScenario(const std::string& s)
{
    if (s == "V2V_Highway")
    {
        return BandwidthPartInfo::V2V_Highway;
    }
    if (s == "V2V_Urban")
    {
        return BandwidthPartInfo::V2V_Urban;
    }
    if (s == "UMi_StreetCanyon")
    {
        return BandwidthPartInfo::UMi_StreetCanyon;
    }
    if (s == "UMa")
    {
        return BandwidthPartInfo::UMa;
    }
    throw std::runtime_error("Unknown --baseScenario: " + s);
}

static SlChannelCond
ParseChannelCond(const std::string& s)
{
    if (s == "los")
    {
        return SlChannelCond::Los;
    }
    if (s == "nlos")
    {
        return SlChannelCond::Nlos;
    }
    if (s == "nlosv")
    {
        return SlChannelCond::Nlosv;
    }
    throw std::runtime_error("--channelCondition must be los | nlos | nlosv (got '" + s + "')");
}

// ---------------------------------------------------------------------------
int
main(int argc, char* argv[])
{
    // ── CLI ────────────────────────────────────────────────────────────────
    uint32_t mcs = 9;
    double distance = 50.0;
    std::string channelCondStr = "los";
    double speedKmh = 0.0;
    bool harq = false;
    uint32_t packetSizeBytes = 100;
    double pdbMs = 10.0;
    double rriMs = 10.0;
    uint32_t psfchPeriod = 4;

    uint32_t mu = 2;
    uint32_t bwPRBs = 10;
    std::string baseScenarioStr = "V2V_Highway";
    bool shadowing = true;
    double channelUpdateMs = -1.0; // <=0 => auto = NR slot (1/2^mu ms)
    double simTime = 12.0;         // per-flow traffic duration (s) -> ~1200 TBs @ 100 pkt/s
    double blockerL = 4.5;
    double blockerW = 2.0;

    uint32_t seed = 1;
    std::string runId = "run0";
    std::string outCsv;

    CommandLine cmd(__FILE__);
    cmd.AddValue("mcs", "Fixed MCS index (AMC disabled)", mcs);
    cmd.AddValue("distance", "Tx-Rx distance (m)", distance);
    cmd.AddValue("channelCondition", "los | nlos | nlosv", channelCondStr);
    cmd.AddValue("speedKmh", "Constant speed of all nodes (km/h)", speedKmh);
    cmd.AddValue("harq", "Enable groupcast HARQ+PSFCH (true) or single-TB (false)", harq);
    cmd.AddValue("packetSizeBytes", "Groupcast packet size (bytes)", packetSizeBytes);
    cmd.AddValue("pdbMs", "Packet Delay Budget (ms)", pdbMs);
    cmd.AddValue("rriMs", "Resource Reservation Interval (ms)", rriMs);
    cmd.AddValue("psfchPeriod", "PSFCH period (slots); 0=disabled, 1,2,4 typical", psfchPeriod);
    cmd.AddValue("mu", "Numerology (0/1/2 = 15/30/60 kHz)", mu);
    cmd.AddValue("bwPRBs", "Subchannel size W in PRBs (~10 -> ~10 subchannels at mu=2)", bwPRBs);
    cmd.AddValue("baseScenario", "3GPP base scenario (V2V_Highway|V2V_Urban|UMi_StreetCanyon|UMa)",
                 baseScenarioStr);
    cmd.AddValue("shadowing", "Enable large-scale shadowing", shadowing);
    cmd.AddValue("channelUpdateMs", "Channel/CCM update period (ms); <=0 => per-slot realtime",
                 channelUpdateMs);
    cmd.AddValue("simTime", "Per-flow traffic duration (s); >=11 ensures >=1000 TBs", simTime);
    cmd.AddValue("blockerL", "Blocker footprint length (m)", blockerL);
    cmd.AddValue("blockerW", "Blocker footprint width (m)", blockerW);
    cmd.AddValue("seed", "RNG run", seed);
    cmd.AddValue("runId", "Unique run identifier", runId);
    cmd.AddValue("outCsv", "One-row-per-run output CSV path", outCsv);
    cmd.Parse(argc, argv);

    SlChannelCond cond;
    BandwidthPartInfo::Scenario baseScenario;
    try
    {
        cond = ParseChannelCond(channelCondStr);
        baseScenario = ParseScenario(baseScenarioStr);
    }
    catch (const std::exception& e)
    {
        std::cerr << "sl_bler_sweep: " << e.what() << "\n";
        return 1;
    }

    // NLOSv requires exactly 1 blocker placed mid-path to guarantee the geometric
    // CCM always returns NLOSv (deterministic). LOS and NLOS need none.
    const uint32_t numBlockers = (cond == SlChannelCond::Nlosv) ? 1u : 0u;

    if (channelUpdateMs <= 0.0)
    {
        channelUpdateMs = 1.0 / static_cast<double>(1u << mu); // NR slot duration (ms)
    }

    RngSeedManager::SetSeed(1);
    RngSeedManager::SetRun(seed);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));

    NS_LOG_INFO("sl_bler_sweep: mcs=" << mcs << " d=" << distance << " cond=" << channelCondStr
                                      << " speed=" << speedKmh << " harq=" << harq
                                      << " pktSize=" << packetSizeBytes << "B"
                                      << " pdb=" << pdbMs << "ms"
                                      << " rri=" << rriMs << "ms"
                                      << " psfch=" << psfchPeriod
                                      << " seed=" << seed);

    // ── 1. Synthetic constant-velocity mobility (no SUMO). ──────────────────
    // Node 0 = Tx, node 1 = Rx; nodes 2.. = blocker-only (no NR device).
    const double zAnt = 1.5;
    const double v = speedKmh / 3.6;

    NodeContainer ueNodes;
    ueNodes.Create(2);
    NodeContainer blockerNodes;
    if (numBlockers > 0)
    {
        blockerNodes.Create(numBlockers);
    }

    MobilityHelper mob;
    mob.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
    mob.Install(ueNodes);
    if (blockerNodes.GetN() > 0)
    {
        mob.Install(blockerNodes);
    }

    auto place = [&](Ptr<Node> n, double x) {
        auto m = n->GetObject<ConstantVelocityMobilityModel>();
        m->SetPosition(Vector(x, 0.0, zAnt));
        m->SetVelocity(Vector(v, 0.0, 0.0));
    };
    place(ueNodes.Get(0), 0.0);
    place(ueNodes.Get(1), distance);
    for (uint32_t i = 0; i < blockerNodes.GetN(); ++i)
    {
        // Uniformly between Tx and Rx, on the line so each footprint blocks.
        place(blockerNodes.Get(i), distance * (i + 1.0) / (numBlockers + 1.0));
    }

    // ── 2. NR/SL stack with channel-condition control. ─────────────────────
    GeomChannelCfg geomCfg;
    geomCfg.blockerLengthM = blockerL;
    geomCfg.blockerWidthM = blockerW;
    geomCfg.blockerNodes = blockerNodes; // empty unless NLOSv

    SlStackParams sp;
    sp.mu = mu;
    sp.bwPRBs = bwPRBs;
    sp.mcs = mcs;
    sp.seed = seed;
    sp.shadowingEnabled = shadowing;
    sp.scenario = baseScenario;
    sp.adaptiveMcs = false; // honour fixed --mcs
    sp.maxNumTx = harq ? 5 : 1;
    sp.psfchPeriod = harq ? psfchPeriod : 0;

    SlStackCtx stack = BuildSlStackBler(sp, geomCfg, cond, channelUpdateMs, ueNodes);

    // ── 3. Single OnOff groupcast flow Tx(0) -> Rx(1) over PC5. ─────────────
    // Construct flow spec dynamically from packet size and RRI.
    // Derive rate_kbps from packet_size_bytes and RRI:
    //   rate_kbps = (packet_size_bytes * 8 bits/byte) / (rri_ms / 1000 s)
    const double rateKbps = (packetSizeBytes * 8.0) / (rriMs / 1000.0);
    std::ostringstream specStream;
    specStream << "label=bler;traffic=onoff;rate_kbps=" << rateKbps
               << ";rri_ms=" << rriMs << ";pdb_ms=" << pdbMs
               << ";port=8001;tx_nodes=0;rxNodes=1";
    const std::string spec = specStream.str();
    std::vector<FlowDef> flowDefs;
    try
    {
        flowDefs = ParseFlowsSpec(spec);
    }
    catch (const std::exception& e)
    {
        std::cerr << "sl_bler_sweep: flow spec error: " << e.what() << "\n";
        return 1;
    }

    FlowResult flows =
        InstallGroupcastFlows(ueNodes, stack, flowDefs, simTime, harq, stack.stream);

    // ── 4. BLER sinks: Tx MAC TB count (node 0) + Rx PSSCH decodes (node 1). ─
    BlerCounter bler;
    Config::ConnectWithoutContext(
        "/NodeList/0/DeviceList/*/$ns3::NrUeNetDevice/ComponentCarrierMapUe/*/"
        "NrUeMac/SlPsschScheduling",
        MakeBoundCallback(&OnPsschTx, &bler));
    Config::ConnectWithoutContext(
        "/NodeList/1/DeviceList/*/$ns3::NrUeNetDevice/ComponentCarrierMapUe/*/"
        "NrUePhy/SpectrumPhy/RxPsschTraceUe",
        MakeBoundCallback(&OnPsschRx, &bler));

    // ── 5. Run. ─────────────────────────────────────────────────────────────
    double simStop = 0.0;
    for (size_t f = 0; f < flows.defs.size(); ++f)
    {
        simStop = std::max(simStop, flows.appStart[f] + simTime + 0.5);
    }
    Simulator::Stop(Seconds(simStop));
    Simulator::Run();
    Simulator::Destroy();

    // ── 6. Output one CSV row. ──────────────────────────────────────────────
    const uint64_t totalTbs  = bler.txTbs;
    const uint64_t rxSuccess = bler.rxSuccess;
    const uint64_t failedTbs = (totalTbs >= rxSuccess) ? (totalTbs - rxSuccess) : 0;
    const double   blerVal   = (totalTbs > 0)
                                   ? static_cast<double>(failedTbs) / static_cast<double>(totalTbs)
                                   : -1.0;
    const double prrVal      = (totalTbs > 0) ? 1.0 - blerVal : -1.0;  // PRR = 1 - BLER
    const double meanSinrDb  = (bler.sinrCount > 0)
                                   ? 10.0 * std::log10(bler.sumSinrLin / bler.sinrCount)
                                   : -999.0;

    // Compute PIR (Packet Inter-Reception time) from successful RX timestamps
    double pirSec = -1.0;
    if (bler.rxSuccessTimes.size() >= 2)
    {
        // Calculate inter-reception gaps between consecutive successful RXs
        double totalGapSec = 0.0;
        for (size_t i = 1; i < bler.rxSuccessTimes.size(); ++i)
        {
            totalGapSec += bler.rxSuccessTimes[i] - bler.rxSuccessTimes[i - 1];
        }
        pirSec = totalGapSec / static_cast<double>(bler.rxSuccessTimes.size() - 1);
    }

    const char* header = "mcs,distance_m,channel_condition,speed_kmh,harq,"
                         "packet_size_bytes,pdb_ms,rri_ms,psfch_period,"
                         "total_tbs,rx_success,failed_tbs,bler,prr,mean_sinr_db,"
                         "pir_sec,used_subch_slots,seed,run_id";
    std::ostringstream row;
    row << mcs << ',' << distance << ',' << channelCondStr << ','
        << speedKmh << ',' << (harq ? 1 : 0) << ','
        << packetSizeBytes << ',' << pdbMs << ',' << rriMs << ',' << psfchPeriod << ','
        << totalTbs << ',' << rxSuccess << ',' << failedTbs << ','
        << blerVal << ',' << prrVal << ',' << meanSinrDb << ','
        << pirSec << ',' << bler.usedSubchSlots << ',' << seed << ',' << runId;

    if (!outCsv.empty())
    {
        std::ofstream o(outCsv);
        if (o.is_open())
        {
            o << header << '\n' << row.str() << '\n';
        }
        else
        {
            std::cerr << "sl_bler_sweep: cannot open outCsv: " << outCsv << "\n";
        }
    }
    std::cout << header << '\n' << row.str() << '\n';

    if (totalTbs < 1000)
    {
        std::cerr << "sl_bler_sweep: WARNING only " << totalTbs
                  << " logical TBs (<1000); increase --simTime.\n";
    }
    return 0;
}
