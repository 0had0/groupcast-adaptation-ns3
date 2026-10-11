// platoon_harq_robustness_eval/platoon_harq_robustness_eval.cc
//
// Robustness of standard NR-V2X Mode-2 groupcast HARQ (feedback Option 1,
// NACK-only, up to 2 retransmissions) for a 9-vehicle platoon, on the highway
// and on the roundabout scenarios of this repo, with and without background
// (surrounding) traffic.
//
//   node 0         platoon leader: the only transmitter of the platoon flow
//                  (groupcast dstL2Id 254, traffic periodic1|periodic2|aperiodic1)
//   nodes 1..8     platoon members: groupcast receivers, NACK on decode failure
//   nodes 9..      background "seats" (see gen_mobility.py): 60 % periodic1 /
//                  40 % aperiodic1, broadcast, no HARQ, each on its own dstL2Id
//
// Channel: 3GPP TR 37.885 V2V_Highway (highway) or V2V_Urban (roundabout, with
// the 98 SUMO buildings registered so NLOS is reachable), geometric NLOSv
// (every vehicle is a blocker), shadowing + fast fading.
//
// HARQ: castType GroupcastNegativeOnly; receivers drop their ACKs
// (common/mcd/mcr_nack_only_gate); the leader treats "no NACK" as success
// (nack_only_harq.h) so a retransmission only happens after a NACK.
//
// KPIs (computed in-run, no SQLite):
//   PRR per member over the KPI window, and every lost (packet, member) pair
//   attributed to exactly one cause:
//     collision : at least one failed transmission attempt of the packet at
//                 that member overlapped in time (same slot) and frequency
//                 (>= 1 common subchannel) with a PSSCH of another vehicle
//     channel   : the packet was transmitted but all its failed attempts were
//                 on clean resources -> propagation (path loss, shadowing,
//                 NLOS / NLOSv blockage, fading)
//     not_tx    : the leader never transmitted the packet (no resource / PDB)
//   plus the resource-level collision rate of the leader's transmissions,
//   HARQ statistics (transmissions per TB, NACKs) and the channel busy ratio.
//
// Outputs (prefix --out):
//   <out>_member.csv   one row per member + one "all" row
//   <out>_run.csv      one row per run
//   <out>_pkt.csv      one row per (packet, member)            [--writePkt]
//   <out>_ltx.csv      one row per leader PSSCH transmission   [--writePkt]

#include "nack_only_harq.h"
#include "sumo_buildings.h"

#include "common/channel/geometric_channel.h"
#include "common/flows/groupcast_flows.h"
#include "common/mcd/mcr_nack_only_gate.h"
#include "common/stack/nr_sl_stack.h"
#include "common/traffic/custom-traffic-application.h"

#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/nr-module.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <numeric>
#include <random>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("PlatoonHarqRobustnessEval");

namespace
{

// ===========================================================================
// mobility trace
// ===========================================================================

struct Wp
{
    double t, x, y;
};

struct Trace
{
    std::map<std::string, std::string> meta;
    std::map<uint32_t, std::vector<Wp>> byNode;
    double tEnd{0.0};
};

Trace
LoadTrace(const std::string& path)
{
    Trace tr;
    std::string cmd = (path.size() > 3 && path.compare(path.size() - 3, 3, ".gz") == 0)
                          ? "gzip -dc '" + path + "'"
                          : "cat '" + path + "'";
    FILE* fp = popen(cmd.c_str(), "r");
    NS_ABORT_MSG_IF(!fp, "cannot open trace " << path);
    char* line = nullptr;
    size_t cap = 0;
    ssize_t len;
    while ((len = getline(&line, &cap, fp)) > 0)
    {
        if (line[0] == '#')
        {
            std::string s(line + 1, static_cast<size_t>(len - 1));
            while (!s.empty() && (s.back() == '\n' || s.back() == '\r'))
            {
                s.pop_back();
            }
            auto b = s.find_first_not_of(' ');
            auto eq = s.find('=');
            if (b != std::string::npos && eq != std::string::npos && eq > b)
            {
                tr.meta[s.substr(b, eq - b)] = s.substr(eq + 1);
            }
            continue;
        }
        if (!(line[0] >= '0' && line[0] <= '9'))
        {
            continue; // header
        }
        double t, x, y, v;
        unsigned nid;
        if (std::sscanf(line, "%lf,%u,%lf,%lf,%lf", &t, &nid, &x, &y, &v) == 5)
        {
            tr.byNode[nid].push_back({t, x, y});
            tr.tEnd = std::max(tr.tEnd, t);
        }
    }
    free(line);
    pclose(fp);
    for (auto& kv : tr.byNode)
    {
        std::stable_sort(kv.second.begin(), kv.second.end(),
                         [](const Wp& a, const Wp& b) { return a.t < b.t; });
    }
    return tr;
}

double
MetaD(const Trace& tr, const std::string& k, double def)
{
    auto it = tr.meta.find(k);
    if (it == tr.meta.end())
    {
        return def;
    }
    try
    {
        return std::stod(it->second);
    }
    catch (...)
    {
        return def;
    }
}

// ===========================================================================
// bookkeeping
// ===========================================================================

struct AnyTx
{
    int64_t slotAbs;
    uint32_t node;
    uint16_t rbStart;
    uint16_t rbLen;
    float x, y;
};

struct MemberRx
{
    int8_t status{0};        //!< 0 = no PSSCH decode attempt (SCI missed), 1 ok, 2 corrupt
    float sinrDb{-99.0f};
};

struct Global
{
    double tSlot{0.0005};
    uint32_t numMembers{8};
    uint32_t numNodes{0};
    uint16_t leaderRnti{0};
    Ptr<NrSlUeMac> leaderMac;
    NodeContainer nodes;

    std::vector<AnyTx> allTx;
    // leader app
    std::vector<double> pktTxTime;   // by seq
    std::vector<uint32_t> pktSize;
    // member app receptions: member m (1..8) -> seq -> rx time
    std::vector<std::unordered_map<uint32_t, double>> rxTime;
    // member PHY: leader-tx index -> member outcome
    std::vector<std::vector<MemberRx>> phy;   // [txIdx][member-1]
    // condition / distance of every leader transmission, per member
    std::vector<std::vector<int8_t>> cond;    // 0 NLOS, 1 NLOSv, 2 LOS
    std::vector<std::vector<float>> dist;
    Ptr<ChannelConditionModel> ccm;
    NackOnlyHarqController* ctl{nullptr};
    // (frame, subframe, slot) -> leader tx indices (for PHY matching)
    std::unordered_map<uint64_t, std::vector<size_t>> txByKey;
    size_t lastSeenTx{0};
    uint64_t phyUnmatched{0};
    // MCS of the leader PSSCHs decoded by the members (self-check of --mcs)
    int mcsRxMin{255};
    int mcsRxMax{-1};
};

Global g;

inline uint64_t
SlotKey(uint32_t frame, uint32_t sf, uint32_t slot)
{
    return (static_cast<uint64_t>(frame) << 24) | (static_cast<uint64_t>(sf) << 16) | slot;
}

void
OnAnyPsschTx(uint32_t nodeIdx, SlPsschUeMacStatParameters p)
{
    const double now = Simulator::Now().GetSeconds();
    Vector pos = g.nodes.Get(nodeIdx)->GetObject<MobilityModel>()->GetPosition();
    g.allTx.push_back({std::llround(now / g.tSlot), nodeIdx, p.rbStart, p.rbLength,
                       static_cast<float>(pos.x), static_cast<float>(pos.y)});
}

/// Runs after the controller's own SlPsschScheduling callback (connected
/// later), so the LeaderTx record of this transmission already exists.
void
OnLeaderPsschTx(SlPsschUeMacStatParameters p)
{
    const auto& txs = g.ctl->Txs();
    if (txs.empty() || p.dstL2Id != 254)
    {
        return;
    }
    const size_t idx = txs.size() - 1;
    g.txByKey[SlotKey(p.frameNum, p.subframeNum, p.slotNum)].push_back(idx);
    g.phy.resize(idx + 1, std::vector<MemberRx>(g.numMembers));
    g.cond.resize(idx + 1, std::vector<int8_t>(g.numMembers, -1));
    g.dist.resize(idx + 1, std::vector<float>(g.numMembers, 0.0f));
    std::vector<int8_t> c(g.numMembers, -1);
    std::vector<float> d(g.numMembers, 0.0f);
    Ptr<MobilityModel> a = g.nodes.Get(0)->GetObject<MobilityModel>();
    for (uint32_t m = 0; m < g.numMembers; ++m)
    {
        Ptr<MobilityModel> b = g.nodes.Get(m + 1)->GetObject<MobilityModel>();
        d[m] = static_cast<float>(a->GetDistanceFrom(b));
        if (g.ccm)
        {
            auto lc = g.ccm->GetChannelCondition(a, b)->GetLosCondition();
            c[m] = (lc == ChannelCondition::LOS) ? 2 : (lc == ChannelCondition::NLOS ? 0 : 1);
        }
    }
    g.cond[idx] = std::move(c);
    g.dist[idx] = std::move(d);
}

void
OnMemberPsschRx(uint32_t member, SlRxDataPacketTraceParams p)
{
    if (!g.leaderMac || p.m_txRnti != g.leaderMac->GetRnti() || p.m_dstL2Id != 254)
    {
        return;
    }
    auto it = g.txByKey.find(SlotKey(p.m_frameNum, p.m_subframeNum, p.m_slotNum));
    if (it == g.txByKey.end())
    {
        ++g.phyUnmatched;
        return;
    }
    // latest leader tx with this (frame, sf, slot) that is not in the future
    const auto& txs = g.ctl->Txs();
    const double now = Simulator::Now().GetSeconds();
    size_t best = SIZE_MAX;
    for (size_t idx : it->second)
    {
        if (txs[idx].tS <= now + 1e-9 && now - txs[idx].tS < 0.01)
        {
            best = idx;
        }
    }
    if (best == SIZE_MAX || best >= g.phy.size())
    {
        ++g.phyUnmatched;
        return;
    }
    g.mcsRxMin = std::min(g.mcsRxMin, static_cast<int>(p.m_mcs));
    g.mcsRxMax = std::max(g.mcsRxMax, static_cast<int>(p.m_mcs));
    MemberRx& r = g.phy[best][member - 1];
    r.status = p.m_corrupt ? 2 : 1;
    r.sinrDb = (p.m_sinr > 0.0) ? static_cast<float>(10.0 * std::log10(p.m_sinr)) : -99.0f;
}

void
OnLeaderAppTx(Ptr<const Packet> pkt,
              const Address& from,
              const Address& to,
              const SeqTsSizeHeader& hdr)
{
    const uint32_t seq = hdr.GetSeq();
    if (seq >= g.pktTxTime.size())
    {
        g.pktTxTime.resize(seq + 1, -1.0);
        g.pktSize.resize(seq + 1, 0);
    }
    g.pktTxTime[seq] = Simulator::Now().GetSeconds();
    g.pktSize[seq] = hdr.GetSize();
}

void
OnMemberAppRx(uint32_t member,
              Ptr<const Packet> pkt,
              const Address& from,
              const Address& to,
              const SeqTsSizeHeader& hdr)
{
    auto& m = g.rxTime[member];
    if (!m.count(hdr.GetSeq()))
    {
        m[hdr.GetSeq()] = Simulator::Now().GetSeconds();
    }
}

std::string
TrafficFlowKeys(const std::string& traffic, bool sps)
{
    // periodic: SPS with RRI = traffic period, PDB = period;
    // aperiodic: dynamic grant, PDB 50 ms (minimum inter-arrival of aperiodic1)
    if (traffic == "periodic1")
    {
        return sps ? "rri_ms=100;pdb_ms=100;dynamic=0" : "rri_ms=0;pdb_ms=100;dynamic=1";
    }
    if (traffic == "periodic2")
    {
        return sps ? "rri_ms=10;pdb_ms=10;dynamic=0" : "rri_ms=0;pdb_ms=10;dynamic=1";
    }
    if (traffic == "aperiodic1")
    {
        return "rri_ms=0;pdb_ms=50;dynamic=1";
    }
    NS_ABORT_MSG("unsupported traffic class " << traffic);
    return "";
}

} // namespace

int
main(int argc, char* argv[])
{
    const auto wall0 = std::chrono::steady_clock::now();

    // -----------------------------------------------------------------------
    // CLI
    // -----------------------------------------------------------------------
    std::string scenario = "highway";
    std::string mobility;
    std::string sumoNet;
    double buildingHeight = 15.0;
    std::string traffic = "periodic1";
    uint32_t numPlatoon = 9;
    double bgPeriodicFrac = 0.6;
    uint32_t seed = 1;
    uint32_t run = 1;
    uint32_t mu = 1;
    double channelBwMHz = 20.0;
    uint32_t subchPrbs = 10;
    uint32_t mcs = 14;
    uint32_t maxNumTx = 3;          // initial + 2 HARQ retransmissions
    uint16_t psfchPeriod = 1;
    uint32_t maxNumPerReserve = 3;
    double mcr = 500.0;
    bool option1Tx = true;
    bool leaderSps = false;         // CTTC SPS never re-selects for a larger SDU (see README)
    bool bgSps = true;
    bool dedicatedCarrier = false;  // every slot SL (else 9 of 20, shared TDD carrier)
    double antennaHeight = 1.5;
    double maxLossDb = 150.0;
    double kpiStart = -1.0;         // default: trace meta kpi_start
    double tailGuard = 0.3;         // packets generated in the last tailGuard s are not counted
    double maxTime = 0.0;           // > 0: truncate the trace (pilots)
    std::string out = "platoon";
    bool writePkt = true;

    // NR RLC UM (TS 38.322) has no t-Reordering: complete SDUs go up as soon
    // as they are received. 5G-LENA's sidelink reuses LTE RLC UM, whose 100 ms
    // t-Reordering holds every SDU behind a lost PDU (tail member, periodic2:
    // median delay 150 ms, prr_pdb ~ 0). 0 ms disables it; PRR is unchanged.
    // Before cmd.Parse so --ns3::LteRlcUm::ReorderingTimer=... still overrides.
    Config::SetDefault("ns3::LteRlcUm::ReorderingTimer", TimeValue(MilliSeconds(0)));

    CommandLine cmd(__FILE__);
    cmd.AddValue("scenario", "highway | roundabout", scenario);
    cmd.AddValue("mobility", "mobility trace (gen_mobility.py output)", mobility);
    cmd.AddValue("sumoNet", "SUMO .net.xml with the buildings (roundabout)", sumoNet);
    cmd.AddValue("buildingHeight", "building height (m)", buildingHeight);
    cmd.AddValue("traffic", "leader traffic: periodic1 | periodic2 | aperiodic1", traffic);
    cmd.AddValue("numPlatoon", "platoon size (leader + members)", numPlatoon);
    cmd.AddValue("bgPeriodicFrac", "fraction of background seats with periodic1", bgPeriodicFrac);
    cmd.AddValue("seed", "RNG seed", seed);
    cmd.AddValue("run", "RNG run", run);
    cmd.AddValue("mu", "numerology", mu);
    cmd.AddValue("channelBwMHz", "SL bandwidth (MHz)", channelBwMHz);
    cmd.AddValue("subchPrbs", "subchannel size (PRBs)", subchPrbs);
    cmd.AddValue("mcs", "fixed MCS (all UEs)", mcs);
    cmd.AddValue("maxNumTx", "sl-MaxTxTransNumPSSCH (1 + max retransmissions)", maxNumTx);
    cmd.AddValue("psfchPeriod", "sl-PSFCH-Period (slots)", psfchPeriod);
    cmd.AddValue("maxNumPerReserve", "sl-MaxNumPerReserve", maxNumPerReserve);
    cmd.AddValue("mcr", "Option-1 communication range requirement (m)", mcr);
    cmd.AddValue("option1Tx", "leader treats 'no NACK' as success (Option 1)", option1Tx);
    cmd.AddValue("leaderSps", "periodic leader traffic uses SPS (else dynamic grant)", leaderSps);
    cmd.AddValue("bgSps", "periodic background traffic uses SPS", bgSps);
    cmd.AddValue("dedicatedCarrier", "dedicated SL carrier: every slot is sidelink", dedicatedCarrier);
    cmd.AddValue("maxLossDb", "SpectrumChannel MaxLossDb", maxLossDb);
    cmd.AddValue("kpiStart", "KPI window start (s); default = trace kpi_start", kpiStart);
    cmd.AddValue("maxTime", "truncate the run at this time (s), 0 = whole trace", maxTime);
    cmd.AddValue("out", "output prefix", out);
    cmd.AddValue("writePkt", "write the per-packet and per-transmission CSVs", writePkt);
    cmd.Parse(argc, argv);

    NS_ABORT_MSG_IF(mobility.empty(), "--mobility is required");
    NS_ABORT_MSG_IF(scenario != "highway" && scenario != "roundabout", "bad --scenario");
    NS_ABORT_MSG_IF(numPlatoon < 2, "--numPlatoon >= 2");
    const uint32_t numMembers = numPlatoon - 1;

    // only read by an "all members must ACK" variant of
    // NrSlUeMacHarq::RecvHarqFeedback; stock CTTC ignores it
    setenv("NUM_MEMBERS", std::to_string(numMembers).c_str(), 1);

    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(run);
    Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(999999999));
    double rlcReorderingMs = -1.0; // the value in effect after the command line
    {
        TypeId::AttributeInformation info;
        if (TypeId::LookupByName("ns3::LteRlcUm").LookupAttributeByName("ReorderingTimer", &info))
        {
            if (auto tv = DynamicCast<const TimeValue>(info.initialValue))
            {
                rlcReorderingMs = tv->Get().GetSeconds() * 1e3;
            }
        }
    }
    Config::SetDefault("ns3::SpectrumChannel::MaxLossDb", DoubleValue(maxLossDb));

    // -----------------------------------------------------------------------
    // mobility
    // -----------------------------------------------------------------------
    Trace tr = LoadTrace(mobility);
    NS_ABORT_MSG_IF(tr.byNode.empty(), "empty trace " << mobility);
    const uint32_t numNodes = tr.byNode.rbegin()->first + 1;
    NS_ABORT_MSG_IF(numNodes < numPlatoon, "trace has fewer nodes than the platoon");
    const uint32_t numBg = numNodes - numPlatoon;
    const double tEnd = (maxTime > 0.0) ? std::min(tr.tEnd, maxTime) : tr.tEnd;
    if (kpiStart < 0.0)
    {
        kpiStart = MetaD(tr, "kpi_start", 3.0);
    }
    NS_ABORT_MSG_IF(kpiStart < 2.2, "kpiStart must leave time for bearer setup (>= 2.2 s)");

    if (scenario == "roundabout" && !sumoNet.empty())
    {
        NS_ABORT_MSG_IF(!SumoNetProjParameter(sumoNet).empty(), "projected SUMO net");
        const uint32_t nb = InstallSumoBuildings(sumoNet, buildingHeight);
        std::cerr << "registered " << nb << " buildings\n";
    }

    NodeContainer nodes;
    nodes.Create(numNodes);
    for (uint32_t i = 0; i < numNodes; ++i)
    {
        Ptr<WaypointMobilityModel> wmm = CreateObject<WaypointMobilityModel>();
        nodes.Get(i)->AggregateObject(wmm);
        const auto it = tr.byNode.find(i);
        const double parkX = -1.0e6 - 2.0e5 * static_cast<double>(i);
        if (it == tr.byNode.end() || it->second.empty())
        {
            wmm->AddWaypoint(Waypoint(Seconds(0.0), Vector(parkX, -1.0e6, antennaHeight)));
            continue;
        }
        if (it->second.front().t > 0.0)
        {
            wmm->AddWaypoint(Waypoint(Seconds(0.0), Vector(parkX, -1.0e6, antennaHeight)));
        }
        double lastT = -1.0;
        for (const Wp& w : it->second)
        {
            if (w.t <= lastT)
            {
                continue;
            }
            wmm->AddWaypoint(Waypoint(Seconds(w.t), Vector(w.x, w.y, antennaHeight)));
            lastT = w.t;
        }
    }
    g.nodes = nodes;
    g.numNodes = numNodes;
    g.numMembers = numMembers;

    // -----------------------------------------------------------------------
    // NR-SL stack
    // -----------------------------------------------------------------------
    GeomChannelCfg geomCfg;
    geomCfg.blockerNodes = nodes;
    geomCfg.blockerLengthM = MetaD(tr, "veh_len_m", 5.0);
    geomCfg.blockerWidthM = 1.8;

    SlStackParams sp;
    sp.mu = mu;
    sp.channelBwMHz = channelBwMHz;
    sp.bwPRBs = subchPrbs;
    sp.numSubchannels = 0; // subchannel size = subchPrbs, count derived from the BW
    sp.mcs = mcs;
    sp.seed = seed;
    sp.maxNumTx = static_cast<uint8_t>(maxNumTx);
    sp.psfchPeriod = psfchPeriod;
    sp.maxNumPerReserve = static_cast<uint8_t>(maxNumPerReserve);
    sp.dedicatedCarrier = dedicatedCarrier;
    sp.harqOption = 1;
    sp.mcrMeters = 0.0; // the Option-1 RX gate is installed below
    sp.adaptiveMcs = false;
    sp.shadowingEnabled = true;
    sp.scenario = (scenario == "highway") ? BandwidthPartInfo::V2V_Highway
                                          : BandwidthPartInfo::V2V_Urban;
    SlStackCtx stack = BuildSlStack(sp, geomCfg, nodes);
    g.ccm = stack.channelConditionModel;

    // Option 1 at the receivers: NACK only, inside the MCR
    InstallMcrNackOnlyGate(stack.ueNetDev, mcr, true);

    // -----------------------------------------------------------------------
    // flows
    // -----------------------------------------------------------------------
    std::mt19937 rng(seed * 2654435761u + run);
    std::uniform_real_distribution<double> uPhase(0.0, 100.0);
    std::ostringstream spec;
    spec << "label=platoon;traffic=" << traffic << ";port=8001;prio=1;tx_nodes=0;rxNodes=";
    for (uint32_t m = 1; m <= numMembers; ++m)
    {
        spec << (m > 1 ? ":" : "") << m;
    }
    spec << ";dstL2Id=254;harqMode=nackonly;" << TrafficFlowKeys(traffic, leaderSps)
         << ";start_ms=" << std::fixed << std::setprecision(3) << 2000.0 + uPhase(rng) / 10.0;

    // background seats: exactly round(frac * N) periodic1, shuffled
    std::vector<int> bgIsP1(numBg, 0);
    const uint32_t nP1 = static_cast<uint32_t>(std::lround(bgPeriodicFrac * numBg));
    for (uint32_t k = 0; k < nP1; ++k)
    {
        bgIsP1[k] = 1;
    }
    std::shuffle(bgIsP1.begin(), bgIsP1.end(), rng);
    for (uint32_t k = 0; k < numBg; ++k)
    {
        const uint32_t nid = numPlatoon + k;
        const std::string tc = bgIsP1[k] ? "periodic1" : "aperiodic1";
        spec << ",label=bg" << k << ";traffic=" << tc << ";port=" << 9000 + k
             << ";prio=3;tx_nodes=" << nid << ";rxNodes=" << nid << ";dstL2Id=" << 1000 + k
             << ";harq=0;" << TrafficFlowKeys(tc, bgSps) << ";start_ms=" << std::fixed
             << std::setprecision(3) << 2000.0 + uPhase(rng);
    }
    std::vector<FlowDef> flowDefs = ParseFlowsSpec(spec.str());
    const double trafficDur = std::max(0.5, tEnd - 2.0);
    FlowResult flows = InstallGroupcastFlows(nodes, stack, flowDefs, trafficDur, true, stack.stream);

    // -----------------------------------------------------------------------
    // HARQ Option-1 controller + traces
    // -----------------------------------------------------------------------
    auto leaderDev = nodes.Get(0)->GetDevice(0)->GetObject<NrUeNetDevice>();
    std::vector<Ptr<NrUeNetDevice>> memberDevs;
    for (uint32_t m = 1; m <= numMembers; ++m)
    {
        memberDevs.push_back(nodes.Get(m)->GetDevice(0)->GetObject<NrUeNetDevice>());
    }
    NackOnlyHarqController ctl;
    ctl.Install(leaderDev, memberDevs, 254, option1Tx, 3 /* SetSlMinTimeGapPsfch(3) */);
    g.ctl = &ctl;
    g.leaderMac = leaderDev->GetMac(0)->GetObject<NrSlUeMac>();
    g.tSlot = ctl.SlotPeriod().GetSeconds();
    // after the controller, so its LeaderTx record exists when this runs
    g.leaderMac->TraceConnectWithoutContext("SlPsschScheduling", MakeCallback(&OnLeaderPsschTx));

    Ptr<CustomTrafficApplication> leaderApp =
        DynamicCast<CustomTrafficApplication>(flows.clients[0].Get(0));
    NS_ABORT_MSG_IF(!leaderApp, "leader app is not a CustomTrafficApplication");
    leaderApp->SetTagDecorator(
        [&ctl](Ptr<Packet> p, uint32_t, uint32_t) { ctl.TagAppPacket(p); });
    leaderApp->TraceConnectWithoutContext("TxWithSeqTsSize", MakeCallback(&OnLeaderAppTx));

    g.rxTime.resize(numPlatoon);
    for (uint32_t a = 0; a < flows.servers[0].GetN(); ++a)
    {
        Ptr<Application> app = flows.servers[0].Get(a);
        const uint32_t nid = app->GetNode()->GetId();
        if (nid >= 1 && nid <= numMembers)
        {
            app->TraceConnectWithoutContext("RxWithSeqTsSize",
                                            MakeBoundCallback(&OnMemberAppRx, nid));
        }
    }
    for (uint32_t m = 1; m <= numMembers; ++m)
    {
        memberDevs[m - 1]->GetPhy(0)->GetSpectrumPhy()->TraceConnectWithoutContext(
            "RxPsschTraceUe",
            MakeBoundCallback(&OnMemberPsschRx, m));
    }
    for (uint32_t i = 0; i < numNodes; ++i)
    {
        auto dev = nodes.Get(i)->GetDevice(0)->GetObject<NrUeNetDevice>();
        dev->GetMac(0)->TraceConnectWithoutContext("SlPsschScheduling",
                                                   MakeBoundCallback(&OnAnyPsschTx, i));
    }
    const uint32_t totalSubch = g.leaderMac->GetTotalSubCh();

    std::cerr << "platoon_harq_robustness_eval: scenario=" << scenario << " traffic=" << traffic
              << " nodes=" << numNodes << " (platoon " << numPlatoon << ", bg seats " << numBg
              << ", periodic1 " << nP1 << ") tEnd=" << tEnd << " kpi=[" << kpiStart << ","
              << tEnd - tailGuard << "] mu=" << mu << " BW=" << channelBwMHz
              << "MHz subch=" << subchPrbs << "PRB" << " mcs=" << mcs << " maxNumTx=" << maxNumTx
              << " psfch=" << psfchPeriod << " option1Tx=" << option1Tx
              << " dedicatedCarrier=" << dedicatedCarrier
              << " rlcReorderingMs=" << rlcReorderingMs << "\n";

    // Local contrib/nr builds may carry NS_LOG_UNCOND debug prints in per-slot
    // paths; with badbit set every std::clog insertion is a no-op, so they
    // cost nothing during the run.
    std::clog.setstate(std::ios_base::badbit);
    Simulator::Stop(Seconds(tEnd + 0.05));
    Simulator::Run();
    std::clog.clear();

    // =======================================================================
    // KPIs
    // =======================================================================
    const auto& ltx = ctl.Txs();
    const double kpiEnd = tEnd - tailGuard;
    std::cerr << "  total subchannels per slot: " << totalSubch << "\n";

    // -- resource collisions of every leader transmission
    std::unordered_map<int64_t, std::vector<size_t>> slotIdx;
    slotIdx.reserve(g.allTx.size());
    for (size_t i = 0; i < g.allTx.size(); ++i)
    {
        slotIdx[g.allTx[i].slotAbs].push_back(i);
    }
    const double activeRadius = 5000.0; // parked seats are > 50 km away
    std::vector<uint16_t> nColl(ltx.size(), 0);
    std::vector<float> nearestCollider(ltx.size(), -1.0f);
    {
        Vector pl;
        for (size_t i = 0; i < ltx.size(); ++i)
        {
            auto it = slotIdx.find(ltx[i].slotAbs);
            if (it == slotIdx.end())
            {
                continue;
            }
            const int a0 = ltx[i].rbStart;
            const int a1 = a0 + ltx[i].rbLen;
            // leader position at this transmission
            const AnyTx* self = nullptr;
            for (size_t j : it->second)
            {
                if (g.allTx[j].node == 0)
                {
                    self = &g.allTx[j];
                }
            }
            for (size_t j : it->second)
            {
                const AnyTx& o = g.allTx[j];
                if (o.node == 0)
                {
                    continue;
                }
                const int b0 = o.rbStart;
                const int b1 = b0 + o.rbLen;
                if (!(a0 < b1 && b0 < a1))
                {
                    continue;
                }
                double d = 0.0;
                if (self)
                {
                    d = std::hypot(o.x - self->x, o.y - self->y);
                }
                if (self && d > activeRadius)
                {
                    continue;
                }
                ++nColl[i];
                if (nearestCollider[i] < 0 || d < nearestCollider[i])
                {
                    nearestCollider[i] = static_cast<float>(d);
                }
            }
        }
    }

    // -- channel busy ratio (all active vehicles, KPI window)
    // busySubchSlots sums every transmission, so overlapping (collided)
    // transmissions count twice: cbr is the offered load and can exceed 1.
    // occupied counts each (slot, subchannel) once: cbr_occupied <= 1.
    double busySubchSlots = 0.0;
    double leaderSubchSlots = 0.0;
    std::unordered_set<int64_t> occupied;
    {
        const int64_t s0 = std::llround(kpiStart / g.tSlot);
        const int64_t s1 = std::llround(kpiEnd / g.tSlot);
        for (const AnyTx& o : g.allTx)
        {
            if (o.slotAbs < s0 || o.slotAbs >= s1 || std::hypot(o.x, o.y) > 5.0e4)
            {
                continue;
            }
            const double sc = static_cast<double>(o.rbLen) / subchPrbs;
            busySubchSlots += sc;
            const uint32_t k0 = o.rbStart / subchPrbs;
            const uint32_t k1 = (o.rbStart + o.rbLen + subchPrbs - 1) / subchPrbs;
            for (uint32_t k = k0; k < k1; ++k)
            {
                occupied.insert(o.slotAbs * 1024 + k);
            }
            if (o.node == 0)
            {
                leaderSubchSlots += sc;
            }
        }
    }
    // SL pool: 9 SL slots out of every 20 physical slots (bitmap 111111000111
    // applied to the 6 UL slots of DL|DL|DL|F|UL|UL|UL|UL|UL|UL), or every
    // slot on the dedicated carrier
    const double slSlotFrac = dedicatedCarrier ? 1.0 : 9.0 / 20.0;
    const double slSlots = (kpiEnd - kpiStart) / g.tSlot * slSlotFrac;
    const double cbr = busySubchSlots / (slSlots * totalSubch);
    const double cbrLeader = leaderSubchSlots / (slSlots * totalSubch);
    const double cbrOccupied = static_cast<double>(occupied.size()) / (slSlots * totalSubch);

    // -- seq -> leader transmissions
    const uint32_t nSeq = static_cast<uint32_t>(g.pktTxTime.size());
    std::vector<std::vector<size_t>> txOfSeq(nSeq);
    for (size_t i = 0; i < ltx.size(); ++i)
    {
        for (uint32_t s : ltx[i].seqs)
        {
            if (s < nSeq)
            {
                txOfSeq[s].push_back(i);
            }
        }
    }

    struct MemberAgg
    {
        uint64_t sent{0}, rx{0}, lostColl{0}, lostChan{0}, lostNoTx{0}, lostOther{0};
        uint64_t attempts{0}, attemptsOk{0}, attemptsCorrupt{0}, attemptsMissed{0};
        double sinrSum{0.0};
        uint64_t sinrN{0};
        double distSum{0.0};
        uint64_t condN[3]{0, 0, 0};
        double delaySum{0.0};
        uint64_t rxInPdb{0};
    };
    std::vector<MemberAgg> agg(numMembers);
    const double pdbS = (traffic == "periodic1") ? 0.1 : (traffic == "periodic2" ? 0.01 : 0.05);

    std::ofstream pktCsv;
    if (writePkt)
    {
        pktCsv.open(out + "_pkt.csv");
        pktCsv << "seq,t_tx,size,member,rx,delay_ms,cause,n_att,n_att_coll,n_att_fail,"
                  "dist_m,cond,best_sinr_db\n";
    }

    uint64_t pktInWin = 0, pktNotTx = 0, pktAnyColl = 0;
    double txPerPkt = 0.0;
    for (uint32_t s = 0; s < nSeq; ++s)
    {
        const double t = g.pktTxTime[s];
        if (t < kpiStart || t >= kpiEnd)
        {
            continue;
        }
        ++pktInWin;
        const auto& atts = txOfSeq[s];
        if (atts.empty())
        {
            ++pktNotTx;
        }
        txPerPkt += static_cast<double>(atts.size());
        bool anyColl = false;
        for (size_t i : atts)
        {
            anyColl = anyColl || nColl[i] > 0;
        }
        pktAnyColl += anyColl ? 1 : 0;

        for (uint32_t m = 1; m <= numMembers; ++m)
        {
            MemberAgg& A = agg[m - 1];
            ++A.sent;
            auto itRx = g.rxTime[m].find(s);
            const bool got = itRx != g.rxTime[m].end();
            uint32_t nFail = 0, nFailColl = 0, nCollAtt = 0;
            float bestSinr = -99.0f;
            std::vector<uint32_t> decodedGen; // TBs this member already decoded
            for (size_t i : atts)
            {
                if (i >= g.phy.size())
                {
                    continue;
                }
                const MemberRx& r = g.phy[i][m - 1];
                if (r.status == 0 && std::find(decodedGen.begin(), decodedGen.end(),
                                               ltx[i].gen) != decodedGen.end())
                {
                    continue; // retransmission of a TB already decoded: not an attempt
                }
                if (r.status == 1)
                {
                    decodedGen.push_back(ltx[i].gen);
                }
                ++A.attempts;
                if (r.status == 1)
                {
                    ++A.attemptsOk;
                }
                else if (r.status == 2)
                {
                    ++A.attemptsCorrupt;
                }
                else
                {
                    ++A.attemptsMissed;
                }
                if (r.status != 0)
                {
                    A.sinrSum += r.sinrDb;
                    ++A.sinrN;
                    bestSinr = std::max(bestSinr, r.sinrDb);
                }
                if (nColl[i] > 0)
                {
                    ++nCollAtt;
                }
                if (r.status != 1)
                {
                    ++nFail;
                    if (nColl[i] > 0)
                    {
                        ++nFailColl;
                    }
                }
            }
            int cause = 0;
            if (got)
            {
                ++A.rx;
                const double dly = itRx->second - t;
                A.delaySum += dly;
                if (dly <= pdbS + 1e-9)
                {
                    ++A.rxInPdb;
                }
            }
            else if (atts.empty())
            {
                cause = 3;
                ++A.lostNoTx;
            }
            else if (nFailColl > 0)
            {
                cause = 1;
                ++A.lostColl;
            }
            else if (nFail > 0)
            {
                cause = 2;
                ++A.lostChan;
            }
            else
            {
                // every attempt decoded at the PHY but the packet never
                // reached the application (RLC reassembly / duplicate)
                cause = 4;
                ++A.lostOther;
            }
            int cnd = -1;
            float dst = -1.0f;
            if (!atts.empty() && atts.front() < g.cond.size())
            {
                cnd = g.cond[atts.front()][m - 1];
                dst = g.dist[atts.front()][m - 1];
                A.distSum += dst;
                if (cnd >= 0)
                {
                    ++A.condN[cnd];
                }
            }
            if (writePkt)
            {
                pktCsv << s << ',' << std::setprecision(4) << t << ',' << g.pktSize[s] << ','
                       << m << ',' << (got ? 1 : 0) << ',' << std::setprecision(2)
                       << (got ? (itRx->second - t) * 1e3 : -1.0) << ',' << cause << ','
                       << atts.size() << ',' << nCollAtt << ',' << nFail << ','
                       << std::setprecision(1) << dst << ',' << cnd << ',' << std::setprecision(1)
                       << bestSinr << '\n';
            }
        }
    }
    if (writePkt)
    {
        pktCsv.close();
        std::ofstream lt(out + "_ltx.csv");
        lt << "t,slot_abs,rb_start,rb_len,harq_id,ndi,rv,tx_in_tb,n_seq,n_nack,decision,"
              "n_coll,nearest_collider_m\n";
        for (size_t i = 0; i < ltx.size(); ++i)
        {
            const LeaderTx& x = ltx[i];
            lt << std::fixed << std::setprecision(5) << x.tS << ',' << x.slotAbs << ','
               << x.rbStart << ',' << x.rbLen << ',' << +x.harqId << ',' << +x.ndi << ','
               << +x.rv << ',' << +x.txInTb << ',' << x.seqs.size() << ',' << +x.nNack << ','
               << +x.decision << ',' << nColl[i] << ',' << std::setprecision(1)
               << nearestCollider[i] << '\n';
        }
    }

    // -- leader transmission statistics (KPI window)
    uint64_t lTx = 0, lTxColl = 0, lNew = 0, lRetx = 0;
    for (size_t i = 0; i < ltx.size(); ++i)
    {
        if (ltx[i].tS < kpiStart || ltx[i].tS >= kpiEnd)
        {
            continue;
        }
        ++lTx;
        lTxColl += nColl[i] > 0 ? 1 : 0;
        if (ltx[i].txInTb == 1)
        {
            ++lNew;
        }
        else
        {
            ++lRetx;
        }
    }

    // -- per-member CSV
    const std::string ivd = tr.meta.count("ivd_m") ? tr.meta["ivd_m"] : "?";
    const std::string nbg = tr.meta.count("nbg_target") ? tr.meta["nbg_target"] : "?";
    const std::string idCols = scenario + "," + ivd + "," + traffic + "," + nbg + "," +
                               std::to_string(seed) + "," + std::to_string(run);
    {
        std::ofstream mc(out + "_member.csv");
        mc << "scenario,ivd_m,traffic,nbg,seed,run,member,sent,rx,prr,prr_pdb,lost_collision,"
              "lost_channel,lost_notx,lost_other,collision_loss_rate,channel_loss_rate,notx_rate,"
              "other_loss_rate,"
              "mean_delay_ms,attempts,att_ok,att_corrupt,att_missed,mean_sinr_db,mean_dist_m,"
              "frac_los,frac_nlosv,frac_nlos\n";
        MemberAgg all;
        for (uint32_t m = 0; m <= numMembers; ++m)
        {
            const MemberAgg& A = (m < numMembers) ? agg[m] : all;
            if (m == numMembers)
            {
                for (const auto& x : agg)
                {
                    all.sent += x.sent;
                    all.rx += x.rx;
                    all.lostColl += x.lostColl;
                    all.lostChan += x.lostChan;
                    all.lostNoTx += x.lostNoTx;
                    all.lostOther += x.lostOther;
                    all.attempts += x.attempts;
                    all.attemptsOk += x.attemptsOk;
                    all.attemptsCorrupt += x.attemptsCorrupt;
                    all.attemptsMissed += x.attemptsMissed;
                    all.sinrSum += x.sinrSum;
                    all.sinrN += x.sinrN;
                    all.distSum += x.distSum;
                    for (int c = 0; c < 3; ++c)
                    {
                        all.condN[c] += x.condN[c];
                    }
                    all.delaySum += x.delaySum;
                    all.rxInPdb += x.rxInPdb;
                }
            }
            const double S = std::max<double>(1.0, static_cast<double>(A.sent));
            const double C = std::max<double>(1.0, static_cast<double>(A.condN[0] + A.condN[1] +
                                                                       A.condN[2]));
            mc << idCols << ',' << (m < numMembers ? std::to_string(m + 1) : std::string("all"))
               << ',' << A.sent << ',' << A.rx << ',' << std::setprecision(6) << A.rx / S << ','
               << A.rxInPdb / S << ',' << A.lostColl << ',' << A.lostChan << ',' << A.lostNoTx
               << ',' << A.lostOther << ',' << A.lostColl / S << ',' << A.lostChan / S << ','
               << A.lostNoTx / S << ',' << A.lostOther / S << ','
               << (A.rx ? A.delaySum / A.rx * 1e3 : 0.0) << ',' << A.attempts << ','
               << A.attemptsOk << ',' << A.attemptsCorrupt << ',' << A.attemptsMissed << ','
               << (A.sinrN ? A.sinrSum / A.sinrN : 0.0) << ','
               << (A.sent ? A.distSum / A.sent : 0.0) << ',' << A.condN[2] / C << ','
               << A.condN[1] / C << ',' << A.condN[0] / C << '\n';
        }
    }

    // -- run CSV
    double prrMean = 0.0;
    double collMean = 0.0;
    double chanMean = 0.0;
    double notxMean = 0.0;
    double otherMean = 0.0;
    double prrMin = 1.0;
    for (const auto& A : agg)
    {
        const double S = std::max<double>(1.0, static_cast<double>(A.sent));
        prrMean += A.rx / S / numMembers;
        collMean += A.lostColl / S / numMembers;
        chanMean += A.lostChan / S / numMembers;
        notxMean += A.lostNoTx / S / numMembers;
        otherMean += A.lostOther / S / numMembers;
        prrMin = std::min(prrMin, A.rx / S);
    }
    const double wall =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - wall0).count();
    {
        std::ofstream rc(out + "_run.csv");
        rc << "scenario,ivd_m,traffic,nbg,seed,run,bg_seats,bg_in_area_mean,mu,bw_mhz,subch_prb,"
              "n_subch,mcs,max_num_tx,psfch_period,kpi_start,kpi_end,pkts,pkts_not_tx,"
              "leader_tx,leader_new_tb,leader_retx,tx_per_pkt,retx_ratio,leader_tx_coll_rate,"
              "pkt_any_coll_rate,nack_rx,implicit_ack,nack_decisions,stale_decisions,"
              "explicit_ack_rx,cbr,cbr_leader,prr_mean,prr_min,collision_loss_rate,"
              "channel_loss_rate,notx_rate,other_loss_rate,phy_unmatched,slot_misaligned,wall_s,"
              "option1_tx,sl_slot_frac,mcs_rx_min,mcs_rx_max,cbr_occupied,rlc_reordering_ms\n";
        rc << idCols << ',' << numBg << ','
           << (tr.meta.count("bg_in_area_mean") ? tr.meta["bg_in_area_mean"] : "0") << ',' << mu
           << ',' << channelBwMHz << ',' << subchPrbs << ',' << totalSubch << ',' << mcs << ','
           << maxNumTx << ',' << psfchPeriod << ',' << kpiStart << ',' << kpiEnd << ','
           << pktInWin << ',' << pktNotTx << ',' << lTx << ',' << lNew << ',' << lRetx << ','
           << std::setprecision(6) << (pktInWin ? txPerPkt / pktInWin : 0.0) << ','
           << (lNew ? static_cast<double>(lRetx) / lNew : 0.0) << ','
           << (lTx ? static_cast<double>(lTxColl) / lTx : 0.0) << ','
           << (pktInWin ? static_cast<double>(pktAnyColl) / pktInWin : 0.0) << ',' << ctl.nNackRx
           << ',' << ctl.nImplicitAck << ',' << ctl.nNackDecision << ',' << ctl.nStale << ','
           << ctl.nAckRx << ',' << cbr << ',' << cbrLeader << ',' << prrMean << ',' << prrMin
           << ',' << collMean << ',' << chanMean << ',' << notxMean << ',' << otherMean << ','
           << g.phyUnmatched
           << ',' << ctl.nSlotMisaligned << ',' << std::fixed << std::setprecision(1) << wall << ','
           << option1Tx << ',' << std::defaultfloat << std::setprecision(4) << slSlotFrac << ',' << g.mcsRxMin << ','
           << g.mcsRxMax << ',' << std::setprecision(6) << cbrOccupied << ','
           << rlcReorderingMs << '\n';
    }

    std::cerr << "  pkts=" << pktInWin << " notTx=" << pktNotTx << " leaderTx=" << lTx
              << " (new " << lNew << ", retx " << lRetx << ") collTx=" << lTxColl
              << " CBR=" << std::setprecision(3) << cbr << " PRR=" << prrMean << " (min "
              << prrMin << ") coll=" << collMean << " chan=" << chanMean << " notx=" << notxMean << " other=" << otherMean
              << " NACK=" << ctl.nNackRx << " implicitACK=" << ctl.nImplicitAck
              << " stale=" << ctl.nStale << " misaligned=" << ctl.nSlotMisaligned
              << " unmatched=" << g.phyUnmatched << " wall=" << std::setprecision(1) << wall
              << "s\n";

    Simulator::Destroy();
    return 0;
}
