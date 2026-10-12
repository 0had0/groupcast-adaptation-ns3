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
#include "common/stack/nr_sl_coop_scheduler.h"
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
#include <functional>
#include <iomanip>
#include <iostream>
#include <map>
#include <numeric>
#include <random>
#include <set>
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

/// Transmissions of the platoon's TBs by one node (leader, or the helper's
/// relayed copies), recorded by a NackOnlyHarqController on that node.
struct TxSide
{
    NackOnlyHarqController* ctl{nullptr};
    uint32_t node{0};
    uint16_t rnti{0};
    // (frame, subframe, slot) -> tx indices (for PHY matching)
    std::unordered_map<uint64_t, std::vector<size_t>> byKey;
    std::vector<std::vector<MemberRx>> phy; // [txIdx][member-1]
    // condition / distance transmitter -> member, per transmission
    std::vector<std::vector<int8_t>> cond;  // 0 NLOS, 1 NLOSv, 2 LOS
    std::vector<std::vector<float>> dist;
    // road segment of the leader / tail at the transmission (Segment())
    std::vector<int8_t> legSeg;
    std::vector<int8_t> tailSeg;
};

/// A NACK a member sent (forwarded by the Option-1 gate to its PHY)
struct NackSent
{
    double t;
    uint32_t member;
    uint16_t txRnti;
    uint8_t harqId;
};

/// An SCI-1A sent by a node (one per PSSCH): its slot (raw SfnSf::Normalize())
/// and the reservations it announces
struct SciTx
{
    int64_t slotRaw;
    uint8_t sbStart;
    uint8_t sbLen;
    uint16_t rsvpMs; // 0 = no periodic reservation (dynamic grant)
    uint8_t gap1;    // retransmission gaps in slots; 0 / 255 = none
    uint8_t gap2;
};

/// A Mode-2 resource selection (NrSlUeMac::SensingAlgorithm trace)
struct SelEvent
{
    int64_t nRaw;      // selection slot n (raw SfnSf::Normalize())
    uint16_t t0;       // sensing window (slots)
    uint8_t tProc0;    // sensing data of the last tProc0 slots is not used
    uint8_t t1;
    uint16_t t2;
    int thrInit;       // RSRP threshold (dBm), before / after the +3 dB relaxation steps
    int thrFinal;
    uint16_t nCandInit; // S_A after step 4 / after step 5 (own transmissions) / left
    uint16_t nCandStep5;
    uint16_t nCandFinal;
};

/// An SCI-1A in a UE's sensing data (NrSlUeMac::SensingDataRx)
struct SensRec
{
    int64_t slotRaw;
    uint8_t sbStart;
    uint8_t sbLen;
    uint16_t rsvpMs;
    uint8_t gap1; // 0 / 255 = none
    uint8_t sb1;
    uint8_t gap2;
    uint8_t sb2;
    float rsrp;
};

/// Sensing-map divergence at one selection of the leader (0) or the helper (1)
struct DivRow
{
    double t;
    int obs;
    uint32_t nSel;   // resources (slot, subchannel) the selecting UE's sensing excludes
    uint32_t nOther; // ... the other UE's sensing data excludes, same window and threshold
    uint32_t nBoth;
    uint32_t nWin;   // (slot, subchannel) units of the selection window
    int8_t legSeg;   // road segment of the leader / tail (roundabout; -1 highway)
    int8_t tailSeg;
};

struct Global
{
    double tSlot{0.0005};
    uint32_t slotsPerSubframe{2};
    bool roundabout{false};
    uint32_t totalSubCh{5};
    uint32_t helperNode{0};
    // sensing data of the leader (0) / helper (1), time order
    std::vector<SensRec> sens[2];
    std::vector<DivRow> div;
    // collCond: (slotAbs, interferer) -> link condition leader / helper -> interferer
    bool collCond{false};
    std::unordered_map<uint64_t, std::pair<int8_t, int8_t>> condOf;
    std::unordered_set<int64_t> condSlotsDone;
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
    // side 0 = leader, side 1 = helper (relayed copies), if any
    TxSide side[2];
    int numSides{1};
    Ptr<ChannelConditionModel> ccm;
    std::unordered_map<uint16_t, uint32_t> nodeOfRnti;
    // SCI-1A decoded by observer 0 (leader) / 1 (helper): tx RNTI -> times (s)
    std::unordered_map<uint16_t, std::vector<double>> sciSeen[2];
    uint64_t coopEv[6]{0, 0, 0, 0, 0, 0}; // NrSlUeMac::CoopRelayEvent counts (helper)
    uint64_t phyUnmatched{0};
    // MCS of the leader PSSCHs decoded by the members (self-check of --mcs)
    int mcsRxMin{255};
    int mcsRxMax{-1};
    // NACKs the members sent (ground truth for the relay check)
    std::vector<NackSent> nackSent;
    // SCI-1A sent by every node, time order: [node]
    std::vector<std::vector<SciTx>> sciTx;
    // raw slot (SfnSf::Normalize()) -> slotAbs = round(t / tSlot) offset, from the
    // SCI traces (checked constant: sfnOffsetMismatch)
    int64_t sfnOffset{0};
    bool sfnOffsetSet{false};
    uint64_t sfnOffsetMismatch{0};
    // resource selections of the leader (0) / helper (1) and the SCIs their MAC
    // had in its sensing data: (raw slot, start subchannel) -> RSRP (dBm)
    std::vector<SelEvent> sel[2];
    std::unordered_map<uint64_t, float> sensedRsrp[2];
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

int8_t Segment(const Vector& p);
inline Vector PosOf(uint32_t node);
void RecordCollCond(int64_t slotAbs);

/// Runs after the side's controller's own SlPsschScheduling callback
/// (connected later), so the LeaderTx record of this transmission exists.
void
OnPlatoonPsschTx(uint32_t sideIdx, SlPsschUeMacStatParameters p)
{
    TxSide& S = g.side[sideIdx];
    const auto& txs = S.ctl->Txs();
    if (txs.empty() || p.dstL2Id != 254)
    {
        return;
    }
    const size_t idx = txs.size() - 1;
    S.byKey[SlotKey(p.frameNum, p.subframeNum, p.slotNum)].push_back(idx);
    S.phy.resize(idx + 1, std::vector<MemberRx>(g.numMembers));
    S.cond.resize(idx + 1, std::vector<int8_t>(g.numMembers, -1));
    S.dist.resize(idx + 1, std::vector<float>(g.numMembers, 0.0f));
    std::vector<int8_t> c(g.numMembers, -1);
    std::vector<float> d(g.numMembers, 0.0f);
    Ptr<MobilityModel> a = g.nodes.Get(S.node)->GetObject<MobilityModel>();
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
    S.cond[idx] = std::move(c);
    S.dist[idx] = std::move(d);
    S.legSeg.resize(idx + 1, -1);
    S.tailSeg.resize(idx + 1, -1);
    S.legSeg[idx] = Segment(PosOf(0));
    S.tailSeg[idx] = Segment(PosOf(g.numMembers));
    if (g.collCond)
    {
        // after every transmitter of this slot has been traced
        Simulator::Schedule(Seconds(g.tSlot * 0.5), &RecordCollCond,
                            std::llround(Simulator::Now().GetSeconds() / g.tSlot));
    }
}

void
OnSciRx(uint32_t observer, SlRxCtrlPacketTraceParams p)
{
    if (!p.m_corrupt)
    {
        g.sciSeen[observer][p.m_txRnti].push_back(Simulator::Now().GetSeconds());
    }
}

void
OnCoopRelay(uint8_t ev, uint16_t, uint8_t, uint64_t)
{
    if (ev < 6)
    {
        ++g.coopEv[ev];
    }
}

/// The Option-1 gate forwards a member's NACK to its PHY (= the member sends it)
void
OnNackSent(uint32_t nodeId, const SlHarqInfo& info)
{
    if (nodeId >= 1 && nodeId <= g.numMembers && !info.IsReceivedOk())
    {
        g.nackSent.push_back({Simulator::Now().GetSeconds(), nodeId, info.m_txRnti,
                              info.m_harqProcessId});
    }
}

inline int64_t
RawSlot(uint32_t frame, uint32_t subframe, uint32_t slot)
{
    return (static_cast<int64_t>(frame) * 10 + subframe) * g.slotsPerSubframe + slot;
}

inline uint64_t
SensKey(int64_t slotRaw, uint32_t sbStart)
{
    return (static_cast<uint64_t>(slotRaw) << 8) | (sbStart & 0xff);
}

/// every node's SCI-1A (one per PSSCH, same slot as its SlPsschScheduling)
void
OnAnyPscchTx(uint32_t nodeIdx, SlPscchUeMacStatParameters p)
{
    const int64_t raw = RawSlot(p.frameNum, p.subframeNum, p.slotNum);
    const int64_t off = std::llround(Simulator::Now().GetSeconds() / g.tSlot) - raw;
    if (!g.sfnOffsetSet)
    {
        g.sfnOffset = off;
        g.sfnOffsetSet = true;
    }
    else if (off != g.sfnOffset)
    {
        ++g.sfnOffsetMismatch;
    }
    g.sciTx[nodeIdx].push_back({raw, static_cast<uint8_t>(p.slPsschSubChStart),
                                static_cast<uint8_t>(p.slPsschSubChLength),
                                p.slResourceReservePeriod, p.gapReTx1, p.gapReTx2});
}

/// road segment of a position on the roundabout (centre (0, 0), ring radius
/// 25 m): 0 ring, 1 north, 2 east, 3 south, 4 west arm; -1 on the highway
int8_t
Segment(const Vector& p)
{
    if (!g.roundabout)
    {
        return -1;
    }
    if (std::hypot(p.x, p.y) <= 35.0)
    {
        return 0;
    }
    if (std::abs(p.y) >= std::abs(p.x))
    {
        return p.y > 0 ? 1 : 3;
    }
    return p.x > 0 ? 2 : 4;
}

inline Vector
PosOf(uint32_t node)
{
    return g.nodes.Get(node)->GetObject<MobilityModel>()->GetPosition();
}

void
OnSensingDataRx(uint32_t obs, const SensingData& d)
{
    g.sens[obs].push_back({static_cast<int64_t>(d.sfn.Normalize()), d.sbChStart, d.sbChLength,
                           d.rsvp, d.gapReTx1, d.sbChStartReTx1, d.gapReTx2, d.sbChStartReTx2,
                           static_cast<float>(d.slRsrp)});
}

/// (slot, subchannel) units of the window [n + t1, n + t2] that the SCIs of v,
/// sensed in [n - t0, n - tProc0), reserve with an RSRP above thr: the
/// periodic reservations (next periods) and the retransmissions the SCIs
/// indicate (same period too, as TS 38.214 sec. 8.1.4)
void
ReservedUnits(const std::vector<SensRec>& v,
              int64_t n,
              const SelEvent& e,
              int thr,
              std::unordered_set<uint64_t>& out)
{
    const int64_t lo = n + e.t1;
    const int64_t hi = n + e.t2;
    auto add = [&](int64_t slot, uint32_t sb0, uint32_t len) {
        if (slot < lo || slot > hi)
        {
            return;
        }
        for (uint32_t k = sb0; k < sb0 + len && k < g.totalSubCh; ++k)
        {
            out.insert((static_cast<uint64_t>(slot) << 8) | k);
        }
    };
    auto it = std::lower_bound(v.begin(), v.end(), n - e.t0,
                               [](const SensRec& r, int64_t x) { return r.slotRaw < x; });
    for (; it != v.end() && n - it->slotRaw > e.tProc0; ++it)
    {
        const SensRec& r = *it;
        if (r.rsrp <= thr)
        {
            continue;
        }
        const bool g1 = r.gap1 != 0 && r.gap1 != 255;
        const bool g2 = r.gap2 != 0 && r.gap2 != 255;
        const int64_t p = static_cast<int64_t>(r.rsvpMs) * g.slotsPerSubframe;
        for (int64_t base = r.slotRaw; base <= hi; base += p)
        {
            if (base != r.slotRaw)
            {
                add(base, r.sbStart, r.sbLen);
            }
            if (g1)
            {
                add(base + r.gap1, r.sb1, r.sbLen);
            }
            if (g2)
            {
                add(base + r.gap2, r.sb2, r.sbLen);
            }
            if (p == 0)
            {
                break;
            }
        }
    }
}

/// collCond: link condition leader / helper -> every other transmitter of this slot
void
RecordCollCond(int64_t slotAbs)
{
    if (!g.condSlotsDone.insert(slotAbs).second)
    {
        return;
    }
    auto cond = [](uint32_t a, uint32_t b) -> int8_t {
        auto lc = g.ccm->GetChannelCondition(g.nodes.Get(a)->GetObject<MobilityModel>(),
                                             g.nodes.Get(b)->GetObject<MobilityModel>())
                      ->GetLosCondition();
        return (lc == ChannelCondition::LOS) ? 2 : (lc == ChannelCondition::NLOS ? 0 : 1);
    };
    for (auto it = g.allTx.rbegin(); it != g.allTx.rend() && it->slotAbs >= slotAbs; ++it)
    {
        const uint32_t j = it->node;
        if (it->slotAbs != slotAbs || j == 0 || j == g.helperNode)
        {
            continue;
        }
        const uint64_t key = (static_cast<uint64_t>(slotAbs) << 16) | j;
        if (!g.condOf.count(key))
        {
            g.condOf[key] = {cond(0, j), g.helperNode ? cond(g.helperNode, j) : int8_t{-1}};
        }
    }
}

/// a Mode-2 resource selection of the leader (0) or the helper (1)
void
OnSensing(uint32_t obs,
          const NrSlUeMac::SensingTraceReport& r,
          const std::list<SlResourceInfo>& candidates,
          const std::list<SensingData>& sensingData,
          const std::list<SfnSf>&)
{
    // with no sensing data the trace reports the initial candidates and the
    // thresholds are not set
    const bool noData = sensingData.empty();
    g.sel[obs].push_back({static_cast<int64_t>(r.m_sfn.Normalize()), r.m_t0, r.m_tProc0, r.m_t1,
                          r.m_t2, noData ? 0 : r.m_initialRsrpThreshold,
                          noData ? 0 : r.m_finalRsrpThreshold, r.m_initialCandidateResourcesSize,
                          noData ? static_cast<uint16_t>(candidates.size())
                                 : r.m_candidateResourcesSizeAfterStep5,
                          static_cast<uint16_t>(candidates.size())});
    for (const SensingData& d : sensingData)
    {
        g.sensedRsrp[obs].emplace(SensKey(static_cast<int64_t>(d.sfn.Normalize()), d.sbChStart),
                                  static_cast<float>(d.slRsrp));
    }
    // sensing-map divergence: what this UE's sensing data excludes vs what the
    // other UE's (leader / helper) would, same window, same RSRP threshold
    if (g.helperNode != 0 && r.m_t2 >= r.m_t1)
    {
        const SelEvent& e = g.sel[obs].back();
        // with no sensing data the trace leaves the thresholds unset: use the last one seen
        static int lastThr = -128;
        if (!noData)
        {
            lastThr = r.m_initialRsrpThreshold;
        }
        const int thr = lastThr;
        std::unordered_set<uint64_t> a, b;
        ReservedUnits(g.sens[obs], e.nRaw, e, thr, a);
        ReservedUnits(g.sens[1 - obs], e.nRaw, e, thr, b);
        uint32_t both = 0;
        for (uint64_t k : a)
        {
            both += b.count(k) ? 1 : 0;
        }
        g.div.push_back({Simulator::Now().GetSeconds(), static_cast<int>(obs),
                         static_cast<uint32_t>(a.size()), static_cast<uint32_t>(b.size()), both,
                         static_cast<uint32_t>(r.m_initialCandidateSlotsSize) * g.totalSubCh,
                         Segment(PosOf(0)), Segment(PosOf(g.numMembers))});
    }
}

/// observer decoded an SCI-1A of node within [t - window, t)
bool
Sensed(uint32_t observer, uint32_t node, double t, double window)
{
    auto itR = std::find_if(g.nodeOfRnti.begin(), g.nodeOfRnti.end(),
                            [node](const auto& kv) { return kv.second == node; });
    if (itR == g.nodeOfRnti.end())
    {
        return false;
    }
    auto it = g.sciSeen[observer].find(itR->first);
    if (it == g.sciSeen[observer].end())
    {
        return false;
    }
    auto lo = std::lower_bound(it->second.begin(), it->second.end(), t - window);
    return lo != it->second.end() && *lo < t;
}

void
OnMemberPsschRx(uint32_t member, SlRxDataPacketTraceParams p)
{
    if (p.m_dstL2Id != 254)
    {
        return;
    }
    int sideIdx = -1;
    for (int k = 0; k < g.numSides; ++k)
    {
        if (p.m_txRnti == g.side[k].rnti)
        {
            sideIdx = k;
        }
    }
    if (sideIdx < 0)
    {
        return;
    }
    TxSide& S = g.side[sideIdx];
    auto it = S.byKey.find(SlotKey(p.m_frameNum, p.m_subframeNum, p.m_slotNum));
    if (it == S.byKey.end())
    {
        ++g.phyUnmatched;
        return;
    }
    // latest tx of this side with this (frame, sf, slot) that is not in the future
    const auto& txs = S.ctl->Txs();
    const double now = Simulator::Now().GetSeconds();
    size_t best = SIZE_MAX;
    for (size_t idx : it->second)
    {
        if (txs[idx].tS <= now + 1e-9 && now - txs[idx].tS < 0.01)
        {
            best = idx;
        }
    }
    if (best == SIZE_MAX || best >= S.phy.size())
    {
        ++g.phyUnmatched;
        return;
    }
    g.mcsRxMin = std::min(g.mcsRxMin, static_cast<int>(p.m_mcs));
    g.mcsRxMax = std::max(g.mcsRxMax, static_cast<int>(p.m_mcs));
    MemberRx& r = S.phy[best][member - 1];
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

// Grants created by the schedulers (each SPS grant = one resource (re)selection)
// and SPS grants that did not fit an RLC SDU (NrSlUeMac::SpsReselectOnMisfit).
// role 0 = leader, 1 = background.
struct GrantEvent
{
    double t{0.0};
    uint8_t role{0};
    bool sps{false};
    uint16_t subch{0};
    uint32_t bytes{0};
    uint8_t slots{0}; //!< slots reserved per period (initial + retransmissions)
};

struct MisfitEvent
{
    double t{0.0};
    uint8_t role{0};
    bool reselected{false};
};

std::vector<GrantEvent> g_grants;
std::vector<std::pair<double, uint8_t>> g_kept; // (t, role): SPS resource kept
std::vector<MisfitEvent> g_misfits;

void
OnGrantCreated(uint32_t role, const NrSlUeMacScheduler::GrantInfo& gi, uint16_t /*psfchPeriod*/)
{
    GrantEvent e;
    e.t = Simulator::Now().GetSeconds();
    e.role = static_cast<uint8_t>(role);
    e.sps = !gi.isDynamic;
    e.slots = gi.nSelected;
    if (!gi.slotAllocations.empty())
    {
        e.subch = gi.slotAllocations.begin()->slPsschSubChLength;
        for (const auto& r : gi.slotAllocations.begin()->slRlcPduInfo)
        {
            e.bytes += r.size;
        }
    }
    g_grants.push_back(e);
}

void
OnSpsKept(uint32_t role, uint32_t /*dstL2Id*/, uint8_t /*counter*/)
{
    g_kept.emplace_back(Simulator::Now().GetSeconds(), static_cast<uint8_t>(role));
}

void
OnSpsMisfit(uint32_t role,
            uint32_t /*dstL2Id*/,
            uint8_t /*lcId*/,
            uint32_t /*bufferSize*/,
            uint32_t /*allocated*/,
            uint32_t /*capacity*/,
            bool reselected)
{
    g_misfits.push_back({Simulator::Now().GetSeconds(), static_cast<uint8_t>(role), reselected});
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
    // Cooperative HARQ (contrib/nr chained reservations, EnableChainedRes):
    //   off       stock leader scheduler, no helper (original campaign)
    //   none      leader-only arm of the cooperative comparison (NrSlCoopScheduler
    //             on the leader with cooperation off)
    //   leaderRes the helper relays in the leader's reserved retransmission slots
    //   ownRes    the helper selects its own resources (Mode 2) after the NACK
    //   split     the leader keeps its initial transmission and 1st retransmission;
    //             the 2nd reserved retransmission slot is handed to the helper, used
    //             only after a NACK on the leader's retransmission
    //   additive  the leader runs unchanged Option-1 HARQ; after a NACK on the
    //             leader's initial transmission the helper adds up to relayBudget
    //             copies on its own (Mode 2) resources
    bool silenceClog = true;        // false: keep NS_LOG output (debugging)
    std::string helperMode = "off";
    uint32_t helper = 0;            // member (1..numPlatoon-1) acting as helper
    uint32_t relayBudget = 1;       // additive: helper copies per TB
    bool collCond = false;          // _coll.csv: link condition leader / helper -> interferer
    // Standard Mode-2 options (all off = stock 5G-LENA behaviour):
    //   spsReselectOnMisfit  SPS reselection when an RLC SDU does not fit the grant
    //                        (TS 38.321 sec. 5.22.1.1) instead of segmentation
    //   slDynExclusion       sensing exclusion for dynamic grants (TS 38.214 sec. 8.1.4
    //                        step 6 with C_resel = 1), "D1"
    //   slProjectAnnounced   projection of the retransmissions announced in the current
    //                        period and by dynamic grants, "D2"
    bool spsReselectOnMisfit = false;
    bool slDynExclusion = false;
    bool slProjectAnnounced = false;
    // SPS configuration: sl-ProbResourceKeep and sl-ResourceReservePeriodList
    // (empty = the stack default: 0, 10-100, 150-1000 ms without 250, 27 values)
    double slProbResourceKeep = 0.0;
    std::string rriListStr;
                                    // (extra channel-condition queries: may perturb the run)

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
    cmd.AddValue("silenceClog", "silence std::clog (NS_LOG) during the run", silenceClog);
    cmd.AddValue("helperMode",
                 "cooperative HARQ: off | none | leaderRes | ownRes | split | additive",
                 helperMode);
    cmd.AddValue("helper", "member acting as helper (also the sensing observer)", helper);
    cmd.AddValue("relayBudget", "additive arm: helper copies per TB", relayBudget);
    cmd.AddValue("collCond",
                 "log the link condition leader / helper -> interferer of collided attempts "
                 "(extra channel-condition queries can change the random draws: step-2 runs)",
                 collCond);
    cmd.AddValue("spsReselectOnMisfit",
                 "SPS: resource reselection when an RLC SDU does not fit the grant "
                 "(TS 38.321 5.22.1.1) instead of segmenting it across periods",
                 spsReselectOnMisfit);
    cmd.AddValue("slDynExclusion",
                 "sensing exclusion also for dynamic grants (TS 38.214 8.1.4, C_resel = 1)",
                 slDynExclusion);
    cmd.AddValue("slProjectAnnounced",
                 "sensing: project announced retransmissions of the current period and of "
                 "dynamic grants",
                 slProjectAnnounced);
    cmd.AddValue("slProbResourceKeep",
                 "sl-ProbResourceKeep: probability of keeping an SPS resource at counter "
                 "expiry (TS 38.321 5.22.1.1, 0..0.8)",
                 slProbResourceKeep);
    cmd.AddValue("rriList",
                 "sl-ResourceReservePeriodList in ms, comma separated (at most 16, RRC); "
                 "empty = stack default (27 values)",
                 rriListStr);
    cmd.Parse(argc, argv);
    // ':'-separated copy for the run CSV
    std::string rriListCsv = rriListStr;
    std::replace(rriListCsv.begin(), rriListCsv.end(), ',', ':');
    NS_ABORT_MSG_IF(slProbResourceKeep < 0.0 || slProbResourceKeep > 0.8,
                    "--slProbResourceKeep must be in [0, 0.8]");
    std::list<uint16_t> rriList;
    {
        std::stringstream ss(rriListStr);
        std::string tok;
        while (std::getline(ss, tok, ','))
        {
            if (!tok.empty())
            {
                rriList.push_back(static_cast<uint16_t>(std::stoul(tok)));
            }
        }
        NS_ABORT_MSG_IF(rriList.size() > 16, "--rriList: at most 16 entries (TS 38.331)");
    }
    Config::SetDefault("ns3::NrSlUeMac::SpsReselectOnMisfit", BooleanValue(spsReselectOnMisfit));
    Config::SetDefault("ns3::NrSlUeMac::SlDynExclusion", BooleanValue(slDynExclusion));
    Config::SetDefault("ns3::NrSlUeMac::SlProjectAnnounced", BooleanValue(slProjectAnnounced));
    // the leader's flow is on SPS only for periodic traffic (TrafficFlowKeys)
    const bool leaderSpsCfg = leaderSps && traffic.rfind("periodic", 0) == 0;

    NS_ABORT_MSG_IF(mobility.empty(), "--mobility is required");
    NS_ABORT_MSG_IF(scenario != "highway" && scenario != "roundabout", "bad --scenario");
    NS_ABORT_MSG_IF(numPlatoon < 2, "--numPlatoon >= 2");
    const uint32_t numMembers = numPlatoon - 1;

    // only read by an "all members must ACK" variant of
    // NrSlUeMacHarq::RecvHarqFeedback; stock CTTC ignores it
    setenv("NUM_MEMBERS", std::to_string(numMembers).c_str(), 1);

    NS_ABORT_MSG_IF(helperMode != "off" && helperMode != "none" && helperMode != "leaderRes" &&
                        helperMode != "ownRes" && helperMode != "split" &&
                        helperMode != "additive",
                    "bad --helperMode " << helperMode);
    const bool coop = (helperMode == "leaderRes" || helperMode == "ownRes" ||
                       helperMode == "split" || helperMode == "additive");
    // the helper selects the resources of its copies itself (else the leader's
    // selection gave them)
    const bool helperSelects = (helperMode == "ownRes" || helperMode == "additive");
    NS_ABORT_MSG_IF(helperMode == "split" && maxNumTx < 3, "split needs --maxNumTx >= 3");
    NS_ABORT_MSG_IF(helperMode == "additive" && relayBudget == 0, "--relayBudget >= 1");
    NS_ABORT_MSG_IF(coop && (helper < 1 || helper > numMembers), "--helper must be 1.." << numMembers);
    NS_ABORT_MSG_IF(helper > numMembers, "--helper must be <= " << numMembers);
    NS_ABORT_MSG_IF(coop && psfchPeriod == 0, "the helper modes are NACK-triggered: PSFCH needed");
    if (coop)
    {
        Config::SetDefault("ns3::NrSlUeMac::EnableChainedRes", BooleanValue(true));
        Config::SetDefault("ns3::NrSlUeMac::HelperNackTriggered", BooleanValue(true));
    }

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
    g.slotsPerSubframe = 1u << mu;
    g.sciTx.resize(numNodes);

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
    sp.probResourceKeep = slProbResourceKeep;
    sp.rriList = rriList;
    sp.shadowingEnabled = true;
    sp.scenario = (scenario == "highway") ? BandwidthPartInfo::V2V_Highway
                                          : BandwidthPartInfo::V2V_Urban;
    SlStackCtx stack = BuildSlStack(sp, geomCfg, nodes);
    g.ccm = stack.channelConditionModel;
    g.roundabout = (scenario == "roundabout");
    g.helperNode = helper;
    g.collCond = collCond;

    // Option 1 at the receivers: NACK only, inside the MCR
    InstallMcrNackOnlyGate(stack.ueNetDev, mcr, true);
    SetMcrNackOnlyGateObserver(MakeCallback(&OnNackSent));

    // RNTIs are assigned once the simulation runs: RNTI-dependent setup is
    // done at t = 1 s (traffic starts at 2 s), see below
    auto rntiOfNode = [&nodes](uint32_t n) {
        return nodes.Get(n)
            ->GetDevice(0)
            ->GetObject<NrUeNetDevice>()
            ->GetMac(0)
            ->GetObject<NrSlUeMac>()
            ->GetRnti();
    };
    Ptr<NrSlCoopScheduler> coopSched;
    // Cooperative comparison: the leader runs NrSlCoopScheduler (= the stock
    // fixed-MCS scheduler plus the chained-reservation logic) in every arm,
    // so the arms differ only by cooperation
    if (helperMode != "off")
    {
        Ptr<NrSlUeMac> lmac =
            nodes.Get(0)->GetDevice(0)->GetObject<NrUeNetDevice>()->GetMac(0)->GetObject<NrSlUeMac>();
        Ptr<NrSlCoopScheduler> cs = CreateObject<NrSlCoopScheduler>();
        cs->SetAttribute("Mcs", UintegerValue(mcs));
        Ptr<NrAmc> amc = CreateObject<NrAmc>();
        amc->SetAttribute("AmcModel", EnumValue(NrAmc::ErrorModel));
        amc->SetAttribute("ErrorModelType", TypeIdValue(NrEesmIrT1::GetTypeId()));
        cs->SetNrSlUeMac(lmac);
        cs->InstallAmc(amc);
        coopSched = cs;
        if (coop)
        {
            cs->SetAttribute("ChainedResActive", BooleanValue(true));
            cs->SetAttribute("LeaderTxCount", UintegerValue(helperMode == "split" ? 2 : 1));
            cs->SetAttribute("HelperOwnResources", BooleanValue(helperMode == "ownRes"));
            if (helperMode == "additive")
            {
                cs->SetAttribute("AdditiveRelays", UintegerValue(relayBudget));
            }
        }
        lmac->SetAttribute("NrSlUeMacScheduler", PointerValue(cs));
        Ptr<NrSlUeMacScheduler> csBase = cs;
        csBase->AssignStreams(stack.stream + 100000);
    }
    // grant statistics of the leader (0) and the background (1); members only relay
    for (uint32_t i = 0; i < nodes.GetN(); ++i)
    {
        if (i > 0 && i < numPlatoon)
        {
            continue;
        }
        Ptr<NrSlUeMac> mac =
            nodes.Get(i)->GetDevice(0)->GetObject<NrUeNetDevice>()->GetMac(0)->GetObject<NrSlUeMac>();
        PointerValue pv;
        mac->GetAttribute("NrSlUeMacScheduler", pv);
        Ptr<NrSlUeMacScheduler> sch = pv.Get<NrSlUeMacScheduler>();
        if (sch)
        {
            const uint32_t role = (i == 0) ? 0 : 1;
            sch->TraceConnectWithoutContext("GrantCreated", MakeBoundCallback(&OnGrantCreated, role));
            sch->TraceConnectWithoutContext("SpsMisfit", MakeBoundCallback(&OnSpsMisfit, role));
            sch->TraceConnectWithoutContext("SpsResourceKept", MakeBoundCallback(&OnSpsKept, role));
        }
    }

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
    ctl.SetSpsSafeAck(leaderSpsCfg);
    g.leaderMac = leaderDev->GetMac(0)->GetObject<NrSlUeMac>();
    g.side[0].ctl = &ctl;
    g.side[0].node = 0;
    g.tSlot = ctl.SlotPeriod().GetSeconds();
    // after the controller, so its LeaderTx record exists when this runs
    g.leaderMac->TraceConnectWithoutContext("SlPsschScheduling",
                                            MakeBoundCallback(&OnPlatoonPsschTx, 0u));
    leaderDev->GetPhy(0)->GetSpectrumPhy()->TraceConnectWithoutContext(
        "RxPscchTraceUe", MakeBoundCallback(&OnSciRx, 0u));
    g.leaderMac->TraceConnectWithoutContext("SensingAlgorithm", MakeBoundCallback(&OnSensing, 0u));
    g.leaderMac->TraceConnectWithoutContext("SensingDataRx",
                                            MakeBoundCallback(&OnSensingDataRx, 0u));

    // helper: an observe-only controller records its relayed copies (packet
    // tags) and the NACKs on them; it never injects feedback (option1Tx false)
    NackOnlyHarqController hctl;
    if (helper > 0)
    {
        auto helperDev = nodes.Get(helper)->GetDevice(0)->GetObject<NrUeNetDevice>();
        helperDev->GetPhy(0)->GetSpectrumPhy()->TraceConnectWithoutContext(
            "RxPscchTraceUe", MakeBoundCallback(&OnSciRx, 1u));
        helperDev->GetMac(0)->TraceConnectWithoutContext("SensingAlgorithm",
                                                         MakeBoundCallback(&OnSensing, 1u));
        helperDev->GetMac(0)->TraceConnectWithoutContext("SensingDataRx",
                                                         MakeBoundCallback(&OnSensingDataRx, 1u));
        if (coop)
        {
            std::vector<Ptr<NrUeNetDevice>> others;
            for (uint32_t m = 1; m <= numMembers; ++m)
            {
                if (m != helper)
                {
                    others.push_back(memberDevs[m - 1]);
                }
            }
            hctl.Install(helperDev, others, 254, false, 3);
            g.side[1].ctl = &hctl;
            g.side[1].node = helper;
            g.numSides = 2;
            Ptr<NrSlUeMac> hmac = helperDev->GetMac(0)->GetObject<NrSlUeMac>();
            hmac->TraceConnectWithoutContext("SlPsschScheduling",
                                             MakeBoundCallback(&OnPlatoonPsschTx, 1u));
            hmac->TraceConnectWithoutContext("CoopRelay", MakeCallback(&OnCoopRelay));
        }
    }

    // poll until every UE has its RNTI (assigned by the MAC at run time)
    double rntiReadyAt = -1.0;
    std::function<void()> rntiSetup = [&, coop]() {
        for (uint32_t i = 0; i < numNodes; ++i)
        {
            if (rntiOfNode(i) == 0)
            {
                Simulator::Schedule(MilliSeconds(1), rntiSetup);
                return;
            }
        }
        rntiReadyAt = Simulator::Now().GetSeconds();
        for (uint32_t i = 0; i < numNodes; ++i)
        {
            g.nodeOfRnti[rntiOfNode(i)] = i;
        }
        g.side[0].rnti = rntiOfNode(0);
        if (helper > 0)
        {
            g.side[1].rnti = rntiOfNode(helper);
            if (coop && coopSched)
            {
                coopSched->SetAttribute("HelperId", UintegerValue(g.side[1].rnti));
            }
        }
    };
    Simulator::Schedule(MilliSeconds(1), rntiSetup);

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
        dev->GetMac(0)->TraceConnectWithoutContext("SlPscchScheduling",
                                                   MakeBoundCallback(&OnAnyPscchTx, i));
    }
    const uint32_t totalSubch = g.leaderMac->GetTotalSubCh();
    g.totalSubCh = totalSubch;

    std::cerr << "platoon_harq_robustness_eval: scenario=" << scenario << " traffic=" << traffic
              << " nodes=" << numNodes << " (platoon " << numPlatoon << ", bg seats " << numBg
              << ", periodic1 " << nP1 << ") tEnd=" << tEnd << " kpi=[" << kpiStart << ","
              << tEnd - tailGuard << "] mu=" << mu << " BW=" << channelBwMHz
              << "MHz subch=" << subchPrbs << "PRB" << " mcs=" << mcs << " maxNumTx=" << maxNumTx
              << " psfch=" << psfchPeriod << " option1Tx=" << option1Tx
              << " dedicatedCarrier=" << dedicatedCarrier
              << " rlcReorderingMs=" << rlcReorderingMs << " helperMode=" << helperMode
              << " helper=" << helper << "\n";

    // Local contrib/nr builds may carry NS_LOG_UNCOND debug prints in per-slot
    // paths; with badbit set every std::clog insertion is a no-op, so they
    // cost nothing during the run.
    if (silenceClog)
    {
        std::clog.setstate(std::ios_base::badbit);
    }
    Simulator::Stop(Seconds(tEnd + 0.05));
    Simulator::Run();
    std::clog.clear();

    // =======================================================================
    // KPIs
    // =======================================================================
    const auto& ltx = ctl.Txs();
    const double kpiEnd = tEnd - tailGuard;

    // grant statistics in the KPI window
    struct GrantAgg
    {
        uint64_t sps{0}, dyn{0}, spsAll{0}, dynAll{0}, misfitResel{0}, misfitEnlarged{0}, kept{0};
        double subchSum{0.0}, bytesSum{0.0}, slotsSum{0.0};
    } gAgg[2];
    for (const auto& e : g_grants)
    {
        GrantAgg& A = gAgg[e.role];
        (e.sps ? A.spsAll : A.dynAll) += 1;
        if (e.t < kpiStart || e.t >= kpiEnd)
        {
            continue;
        }
        (e.sps ? A.sps : A.dyn) += 1;
        A.subchSum += e.subch;
        A.bytesSum += e.bytes;
        A.slotsSum += e.slots;
    }
    for (const auto& e : g_misfits)
    {
        if (e.t >= kpiStart && e.t < kpiEnd)
        {
            (e.reselected ? gAgg[e.role].misfitResel : gAgg[e.role].misfitEnlarged) += 1;
        }
    }
    for (const auto& [t, role] : g_kept)
    {
        if (t >= kpiStart && t < kpiEnd)
        {
            ++gAgg[role].kept;
        }
    }
    const double winS = std::max(1e-9, kpiEnd - kpiStart);
    // effective grant type of the leader's flow
    const std::string leaderGrant = gAgg[0].spsAll > 0   ? "sps"
                                    : gAgg[0].dynAll > 0 ? "dynamic"
                                    : (leaderSpsCfg ? "sps" : "dynamic");
    std::cerr << "  total subchannels per slot: " << totalSubch << "\n";

    // -- all transmissions of the platoon's TBs: the leader's, and the helper's
    //    relayed copies, which belong to the TB of the leader transmission they copy
    struct UTx
    {
        int side;
        size_t idx;
        uint32_t node;
        const LeaderTx* x;
        int64_t tb; // shared by every copy of a TB
    };
    std::vector<UTx> utx;
    for (size_t i = 0; i < ltx.size(); ++i)
    {
        utx.push_back({0, i, 0, &ltx[i], static_cast<int64_t>(ltx[i].gen)});
    }
    if (g.numSides > 1)
    {
        std::map<std::vector<uint32_t>, std::vector<size_t>> ltxOfSeqs;
        for (size_t i = 0; i < ltx.size(); ++i)
        {
            ltxOfSeqs[ltx[i].seqs].push_back(i);
        }
        const auto& htx = hctl.Txs();
        for (size_t j = 0; j < htx.size(); ++j)
        {
            int64_t tb = -1 - static_cast<int64_t>(j);
            auto it = ltxOfSeqs.find(htx[j].seqs);
            if (it != ltxOfSeqs.end())
            {
                for (size_t i : it->second)
                {
                    if (ltx[i].tS < htx[j].tS)
                    {
                        tb = ltx[i].gen; // latest earlier leader TB with these packets
                    }
                }
            }
            utx.push_back({1, j, helper, &htx[j], tb});
        }
    }
    std::stable_sort(utx.begin(), utx.end(), [](const UTx& a, const UTx& b) {
        return a.x->tS < b.x->tS;
    });
    std::vector<size_t> uOf[2];
    uOf[0].assign(ltx.size(), SIZE_MAX);
    uOf[1].assign(g.numSides > 1 ? hctl.Txs().size() : 0, SIZE_MAX);
    for (size_t u = 0; u < utx.size(); ++u)
    {
        uOf[utx[u].side][utx[u].idx] = u;
    }
    auto phyOf = [&](size_t u, uint32_t m) -> const MemberRx* {
        const TxSide& S = g.side[utx[u].side];
        return utx[u].idx < S.phy.size() ? &S.phy[utx[u].idx][m - 1] : nullptr;
    };

    // -- resource collisions of every transmission (interferers kept for the
    //    sensing attribution)
    std::unordered_map<int64_t, std::vector<size_t>> slotIdx;
    slotIdx.reserve(g.allTx.size());
    for (size_t i = 0; i < g.allTx.size(); ++i)
    {
        slotIdx[g.allTx[i].slotAbs].push_back(i);
    }
    const double activeRadius = 5000.0; // parked seats are > 50 km away
    std::vector<uint16_t> nColl(utx.size(), 0);
    std::vector<float> nearestCollider(utx.size(), -1.0f);
    std::vector<std::vector<std::pair<uint32_t, float>>> interferers(utx.size());
    for (size_t u = 0; u < utx.size(); ++u)
    {
        const LeaderTx& x = *utx[u].x;
        auto it = slotIdx.find(x.slotAbs);
        if (it == slotIdx.end())
        {
            continue;
        }
        const int a0 = x.rbStart;
        const int a1 = a0 + x.rbLen;
        // transmitter position at this transmission
        const AnyTx* self = nullptr;
        for (size_t j : it->second)
        {
            if (g.allTx[j].node == utx[u].node)
            {
                self = &g.allTx[j];
            }
        }
        for (size_t j : it->second)
        {
            const AnyTx& o = g.allTx[j];
            if (o.node == utx[u].node)
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
            ++nColl[u];
            interferers[u].emplace_back(o.node, static_cast<float>(d));
            if (nearestCollider[u] < 0 || d < nearestCollider[u])
            {
                nearestCollider[u] = static_cast<float>(d);
            }
        }
    }

    // -- NACKs the members sent, per transmission: a NACK refers to the latest
    //    transmission of its transmitter (leader, or the helper for its own
    //    copies) with that HARQ id received just before it is enqueued
    std::vector<uint16_t> nNackSent(utx.size(), 0);
    {
        // a helper copy carries the leader's HARQ id in its SCI-2A (the members combine
        // it in the leader's process), so the NACKs on it carry that id, not the
        // helper's own HARQ id of the relay grant
        std::map<std::pair<int, uint8_t>, std::vector<size_t>> txOfHarq; // (side, harq) -> u
        for (size_t u = 0; u < utx.size(); ++u)
        {
            uint8_t h = utx[u].x->harqId;
            if (utx[u].side == 1 && utx[u].tb >= 0 && static_cast<size_t>(utx[u].tb) < ltx.size())
            {
                h = ltx[utx[u].tb].harqId;
            }
            txOfHarq[{utx[u].side, h}].push_back(u); // utx is in time order
        }
        for (const NackSent& k : g.nackSent)
        {
            int side = -1;
            for (int s = 0; s < g.numSides; ++s)
            {
                if (g.side[s].rnti == k.txRnti)
                {
                    side = s;
                }
            }
            auto it = txOfHarq.find({side, k.harqId});
            if (side < 0 || it == txOfHarq.end())
            {
                continue;
            }
            for (auto rit = it->second.rbegin(); rit != it->second.rend(); ++rit)
            {
                const double dt = k.t - utx[*rit].x->tS;
                if (dt >= 0.0)
                {
                    if (dt < 0.005)
                    {
                        ++nNackSent[*rit];
                    }
                    break;
                }
            }
        }
    }

    // -- why did a collided attempt collide although we sense? For attempt u
    //    and interferer j: the selection that chose u's resource (the leader's
    //    for its own TBs and for the relays in its reserved slots, the helper's
    //    for relays on its own resources) and the SCIs j sent before u that
    //    announced u's slot (a periodic reservation, or a retransmission gap)
    enum CollClass
    {
        CC_FRESH_DYNAMIC = 0, // j's transmission was never announced: new dynamic grant
        CC_FRESH_SPS,         // ... new SPS reservation (first one, or reselection)
        CC_FRESH_PLATOON,     // ... a platoon node (leader / helper)
        CC_ANNOUNCED_AFTER,   // announced, but after our selection (or < Tproc0 before it)
        CC_BEFORE_WINDOW,     // announced only before our sensing window
        CC_RETX_NOT_PROJECTED,// announced before our selection only as a retransmission
                              // gap (TRIV) of the same period: 5G-LENA does not exclude it
        CC_BELOW_THRESHOLD,   // periodic reservation sensed, RSRP <= initial threshold
        CC_RELAXED_THRESHOLD, // ... RSRP above the initial but below the relaxed threshold
        CC_HALF_DUPLEX,       // ... not decoded: the selecting UE transmitted in that slot
        CC_SCI_NOT_RECEIVED,  // ... not decoded otherwise (path loss, SCI collision)
        CC_NOT_EXCLUDED,      // ... decoded above the final threshold, still selected
        CC_NO_SELECTION,      // no selection event found for u (should not happen)
        CC_N
    };
    static const char* ccName[CC_N] = {
        "fresh_dynamic", "fresh_sps", "fresh_platoon", "announced_after", "before_window",
        "retx_not_projected", "below_threshold", "relaxed_threshold", "half_duplex",
        "sci_not_received", "not_excluded", "no_selection"};
    struct CollWhy
    {
        int cls{CC_NO_SELECTION};
        int obs{0};          // selecting UE: 0 leader, 1 helper
        int sMinusN{-1};     // slots from our selection to the attempt
        int nMinusA{-1};     // slots from the announcement to our selection
        float rsrp{NAN};     // RSRP of the announcing SCI in the selecting UE's sensing data
        int thrInit{0};
        int thrFinal{0};
    };
    const int64_t off = g.sfnOffset;
    const int64_t slotsPerMs = g.slotsPerSubframe;
    auto sentSciAt = [](uint32_t node, int64_t raw) {
        const auto& v = g.sciTx[node];
        auto it = std::lower_bound(v.begin(), v.end(), raw,
                                   [](const SciTx& c, int64_t r) { return c.slotRaw < r; });
        return it != v.end() && it->slotRaw == raw;
    };
    std::map<std::pair<size_t, uint32_t>, CollWhy> whyCache;
    auto collWhy = [&](size_t u, uint32_t j) -> CollWhy {
        auto key = std::make_pair(u, j);
        auto itc = whyCache.find(key);
        if (itc != whyCache.end())
        {
            return itc->second;
        }
        CollWhy w;
        const int64_t s = utx[u].x->slotAbs - off; // raw slot of the attempt
        w.obs = (utx[u].side == 1 && helperSelects) ? 1 : 0;
        const uint32_t selNode = (w.obs == 1) ? helper : 0;
        // the leader selects all slots of a TB at once: look it up from the TB's
        // first transmission
        int64_t sRef = s;
        if (w.obs == 0)
        {
            const int64_t tb = utx[u].tb;
            if (tb >= 0 && static_cast<size_t>(tb) < ltx.size())
            {
                sRef = ltx[tb].slotAbs - off;
            }
        }
        const auto& ev = g.sel[w.obs];
        auto ie = std::lower_bound(ev.begin(), ev.end(), sRef,
                                   [](const SelEvent& e, int64_t r) { return e.nRaw < r; });
        if (ie == ev.begin() || sRef > std::prev(ie)->nRaw + std::prev(ie)->t2)
        {
            whyCache[key] = w;
            return w; // CC_NO_SELECTION
        }
        const SelEvent& e = *std::prev(ie);
        const int64_t n = e.nRaw;
        w.sMinusN = static_cast<int>(s - n);
        w.thrInit = e.thrInit;
        w.thrFinal = e.thrFinal;
        // SCIs of j before the attempt that announce its slot
        struct Ann
        {
            int64_t a;
            bool periodic; // projected by 5G-LENA (next-period reservation)
            uint8_t sbStart;
        };
        std::vector<Ann> anns;
        const auto& sv = g.sciTx[j];
        auto is = std::lower_bound(sv.begin(), sv.end(), s,
                                   [](const SciTx& c, int64_t r) { return c.slotRaw < r; });
        for (auto it = std::make_reverse_iterator(is); it != sv.rend(); ++it)
        {
            const SciTx& c = *it;
            if (s - c.slotRaw > 1100 * slotsPerMs)
            {
                break;
            }
            const bool g1 = c.gap1 != 0 && c.gap1 != 255;
            const bool g2 = c.gap2 != 0 && c.gap2 != 255;
            if (c.rsvpMs > 0)
            {
                const int64_t p = c.rsvpMs * slotsPerMs;
                if (c.slotRaw + p == s || (g1 && c.slotRaw + p + c.gap1 == s) ||
                    (g2 && c.slotRaw + p + c.gap2 == s))
                {
                    anns.push_back({c.slotRaw, true, c.sbStart});
                    continue;
                }
            }
            if ((g1 && c.slotRaw + c.gap1 == s) || (g2 && c.slotRaw + c.gap2 == s))
            {
                anns.push_back({c.slotRaw, false, c.sbStart});
            }
        }
        if (anns.empty())
        {
            if (j <= numMembers)
            {
                w.cls = CC_FRESH_PLATOON;
            }
            else
            {
                w.cls = bgIsP1[j - numPlatoon] ? CC_FRESH_SPS : CC_FRESH_DYNAMIC;
            }
            whyCache[key] = w;
            return w;
        }
        std::vector<Ann> usable;
        bool anyBefore = false;
        for (const Ann& an : anns)
        {
            if (n - an.a > e.tProc0)
            {
                anyBefore = true;
                if (an.a >= n - e.t0)
                {
                    usable.push_back(an);
                }
            }
        }
        if (!anyBefore)
        {
            w.cls = CC_ANNOUNCED_AFTER;
            w.nMinusA = static_cast<int>(n - anns.back().a);
        }
        else if (usable.empty())
        {
            w.cls = CC_BEFORE_WINDOW;
        }
        else
        {
            const Ann* best = nullptr;
            bool decoded = false;
            bool halfDuplex = false;
            for (const Ann& an : usable)
            {
                if (!an.periodic)
                {
                    continue;
                }
                auto ir = g.sensedRsrp[w.obs].find(SensKey(an.a, an.sbStart));
                if (ir != g.sensedRsrp[w.obs].end())
                {
                    if (!decoded || ir->second > w.rsrp)
                    {
                        w.rsrp = ir->second;
                        best = &an;
                    }
                    decoded = true;
                }
                else if (sentSciAt(selNode, an.a))
                {
                    halfDuplex = true;
                    best = decoded ? best : &an;
                }
                else if (!best)
                {
                    best = &an;
                }
            }
            if (!best)
            {
                w.cls = CC_RETX_NOT_PROJECTED; // every usable announcement is a same-period gap
                w.nMinusA = static_cast<int>(n - usable.front().a);
            }
            else
            {
                w.nMinusA = static_cast<int>(n - best->a);
                if (decoded)
                {
                    w.cls = (w.rsrp > e.thrFinal)  ? CC_NOT_EXCLUDED
                            : (w.rsrp > e.thrInit) ? CC_RELAXED_THRESHOLD
                                                   : CC_BELOW_THRESHOLD;
                }
                else
                {
                    w.cls = halfDuplex ? CC_HALF_DUPLEX : CC_SCI_NOT_RECEIVED;
                }
            }
        }
        whyCache[key] = w;
        return w;
    };
    // counts over (attempt, interferer) pairs of collided attempts that failed at
    // >= 1 member, by role (0 leader / 1 helper copy); "both" = the pairs whose
    // interferer had been sensed by both the leader and the helper
    uint64_t ccAll[2][CC_N] = {};
    uint64_t ccBoth[2][CC_N] = {};
    std::set<std::pair<size_t, uint32_t>> ccSeen;

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

    // -- seq -> transmissions (time order)
    const uint32_t nSeq = static_cast<uint32_t>(g.pktTxTime.size());
    std::vector<std::vector<size_t>> txOfSeq(nSeq);
    for (size_t u = 0; u < utx.size(); ++u)
    {
        for (uint32_t s : utx[u].x->seqs)
        {
            if (s < nSeq)
            {
                txOfSeq[s].push_back(u);
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
        pktCsv << std::fixed; // t_tx 0.1 ms, delay_ms 1 us, dist / SINR 0.1
        pktCsv << "seq,t_tx,size,member,rx,delay_ms,cause,n_att,n_att_coll,n_att_fail,"
                  "dist_m,cond,best_sinr_db\n";
    }

    // collided failed attempts by role (0 leader, 1 helper) and by who had
    // sensed (decoded an SCI-1A of) an interferer in the sensing window before
    // the attempt: [0] neither, [1] helper only, [2] leader only, [3] both
    uint64_t collCat[2][4] = {{0, 0, 0, 0}, {0, 0, 0, 0}};
    const double senseWin = 0.1 /* sensing window */ + pdbS /* selection window */;
    std::ofstream collCsv;
    if (writePkt)
    {
        collCsv.open(out + "_coll.csv");
        collCsv << "t,role,member,seq,interferer,d_tx_interferer_m,sensed_by_leader,"
                   "sensed_by_helper,interferer_type,why,selector,s_minus_n,n_minus_a,"
                   "rsrp_dbm,thr_init_dbm,thr_final_dbm,cond_leader_interf,cond_helper_interf,"
                   "leader_seg,tail_seg,turn\n";
    }
    uint64_t helperAtt = 0, helperAttOk = 0, helperRecovered = 0;
    uint64_t helperAttAfterFail = 0, helperAttFirst = 0;

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
        for (size_t u : atts)
        {
            anyColl = anyColl || nColl[u] > 0;
        }
        pktAnyColl += anyColl ? 1 : 0;

        for (uint32_t m = 1; m <= numMembers; ++m)
        {
            MemberAgg& A = agg[m - 1];
            ++A.sent;
            auto itRx = g.rxTime[m].find(s);
            const bool got = itRx != g.rxTime[m].end();
            uint32_t nFail = 0, nFailColl = 0, nCollAtt = 0, nAtt = 0;
            float bestSinr = -99.0f;
            std::vector<int64_t> decodedTb; // TBs this member already decoded
            std::vector<int64_t> failedTb;  // TBs with a failed attempt at this member
            int firstAtt = -1;
            for (size_t u : atts)
            {
                if (utx[u].node == m)
                {
                    continue; // the helper's own copy is not an attempt for itself
                }
                const MemberRx* rp = phyOf(u, m);
                if (!rp)
                {
                    continue;
                }
                const MemberRx& r = *rp;
                const int64_t tb = utx[u].tb;
                if (r.status == 0 &&
                    std::find(decodedTb.begin(), decodedTb.end(), tb) != decodedTb.end())
                {
                    continue; // retransmission of a TB already decoded: not an attempt
                }
                if (firstAtt < 0)
                {
                    firstAtt = static_cast<int>(u);
                }
                ++nAtt;
                if (utx[u].side == 1)
                {
                    ++helperAtt;
                    // the member had a failed attempt of this TB before: the copy is
                    // soft-combined with it; else the copy is decoded alone
                    if (std::find(failedTb.begin(), failedTb.end(), tb) != failedTb.end())
                    {
                        ++helperAttAfterFail;
                    }
                    else
                    {
                        ++helperAttFirst;
                    }
                    if (r.status == 1)
                    {
                        ++helperAttOk;
                        if (std::find(failedTb.begin(), failedTb.end(), tb) != failedTb.end())
                        {
                            ++helperRecovered; // decoded only thanks to the helper's copy
                        }
                    }
                }
                if (r.status == 1)
                {
                    decodedTb.push_back(tb);
                }
                else
                {
                    failedTb.push_back(tb);
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
                if (nColl[u] > 0)
                {
                    ++nCollAtt;
                }
                if (r.status != 1)
                {
                    ++nFail;
                    if (nColl[u] > 0)
                    {
                        ++nFailColl;
                        const double tA = utx[u].x->tS;
                        bool sl = false;
                        bool sh = false;
                        for (const auto& [j, dj] : interferers[u])
                        {
                            const bool a = Sensed(0, j, tA, senseWin);
                            const bool b = helper > 0 && Sensed(1, j, tA, senseWin);
                            sl = sl || a;
                            sh = sh || b;
                            const CollWhy w = collWhy(u, j);
                            if (tA >= kpiStart && tA < kpiEnd && ccSeen.insert({u, j}).second)
                            {
                                ++ccAll[utx[u].side][w.cls];
                                if (a && b)
                                {
                                    ++ccBoth[utx[u].side][w.cls];
                                }
                            }
                            if (writePkt && tA >= kpiStart && tA < kpiEnd)
                            {
                                const char* jType = (j == 0)             ? "leader"
                                                    : (j <= numMembers)  ? "member"
                                                    : bgIsP1[j - numPlatoon] ? "bg_sps"
                                                                         : "bg_dynamic";
                                collCsv << std::fixed << std::setprecision(5) << tA << ','
                                        << utx[u].side << ',' << m << ',' << s << ',' << j << ','
                                        << std::setprecision(1) << dj << ',' << a << ',' << b
                                        << ',' << jType << ',' << ccName[w.cls] << ',' << w.obs
                                        << ',' << w.sMinusN << ',' << w.nMinusA << ',';
                                if (std::isnan(w.rsrp))
                                {
                                    collCsv << ',';
                                }
                                else
                                {
                                    collCsv << w.rsrp << ',';
                                }
                                collCsv << w.thrInit << ',' << w.thrFinal << ',';
                                // link condition leader / helper -> interferer (collCond),
                                // road segments of leader and tail, turn phase = different
                                int cl = -1;
                                int ch = -1;
                                auto ic = g.condOf.find(
                                    (static_cast<uint64_t>(utx[u].x->slotAbs) << 16) | j);
                                if (ic != g.condOf.end())
                                {
                                    cl = ic->second.first;
                                    ch = ic->second.second;
                                }
                                const TxSide& SS = g.side[utx[u].side];
                                const int ls =
                                    utx[u].idx < SS.legSeg.size() ? SS.legSeg[utx[u].idx] : -1;
                                const int ts =
                                    utx[u].idx < SS.tailSeg.size() ? SS.tailSeg[utx[u].idx] : -1;
                                collCsv << cl << ',' << ch << ',' << ls << ',' << ts << ','
                                        << ((ls >= 0 && ts >= 0 && ls != ts) ? 1 : 0) << '\n';
                            }
                        }
                        ++collCat[utx[u].side][(sl ? 2 : 0) + (sh ? 1 : 0)];
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
            if (firstAtt >= 0)
            {
                const TxSide& S = g.side[utx[firstAtt].side];
                if (utx[firstAtt].idx < S.cond.size())
                {
                    cnd = S.cond[utx[firstAtt].idx][m - 1];
                    dst = S.dist[utx[firstAtt].idx][m - 1];
                    A.distSum += dst;
                    if (cnd >= 0)
                    {
                        ++A.condN[cnd];
                    }
                }
            }
            if (writePkt)
            {
                pktCsv << s << ',' << std::setprecision(4) << t << ',' << g.pktSize[s] << ','
                       << m << ',' << (got ? 1 : 0) << ',' << std::setprecision(3)
                       << (got ? (itRx->second - t) * 1e3 : -1.0) << ',' << cause << ','
                       << nAtt << ',' << nCollAtt << ',' << nFail << ','
                       << std::setprecision(1) << dst << ',' << cnd << ',' << std::setprecision(1)
                       << bestSinr << '\n';
            }
        }
    }
    auto writeTx = [&](const std::string& fname, int sideIdx) {
        const auto& txs = g.side[sideIdx].ctl->Txs();
        std::ofstream lt(fname);
        lt << "t,slot_abs,rb_start,rb_len,harq_id,ndi,rv,tx_in_tb,n_seq,n_nack,decision,"
              "n_coll,nearest_collider_m,n_nack_sent\n";
        for (size_t i = 0; i < txs.size(); ++i)
        {
            const LeaderTx& x = txs[i];
            const size_t u = uOf[sideIdx][i];
            lt << std::fixed << std::setprecision(5) << x.tS << ',' << x.slotAbs << ','
               << x.rbStart << ',' << x.rbLen << ',' << +x.harqId << ',' << +x.ndi << ','
               << +x.rv << ',' << +x.txInTb << ',' << x.seqs.size() << ',' << +x.nNack << ','
               << +x.decision << ',' << nColl[u] << ',' << std::setprecision(1)
               << nearestCollider[u] << ',' << (u < utx.size() ? nNackSent[u] : 0) << '\n';
        }
    };
    if (writePkt)
    {
        pktCsv.close();
        collCsv.close();
        {
            std::ofstream nk(out + "_nack.csv");
            nk << "t,member,tx_rnti,harq_id\n";
            for (const NackSent& k : g.nackSent)
            {
                nk << std::fixed << std::setprecision(6) << k.t << ',' << k.member << ','
                   << k.txRnti << ',' << +k.harqId << '\n';
            }
        }
        if (!g.div.empty())
        {
            std::ofstream dv(out + "_div.csv");
            dv << "t,selector,n_sel,n_other,n_both,n_window,leader_seg,tail_seg\n";
            for (const DivRow& d : g.div)
            {
                dv << std::fixed << std::setprecision(4) << d.t << ',' << d.obs << ',' << d.nSel
                   << ',' << d.nOther << ',' << d.nBoth << ',' << d.nWin << ',' << +d.legSeg
                   << ',' << +d.tailSeg << '\n';
            }
        }
        writeTx(out + "_ltx.csv", 0);
        if (g.numSides > 1)
        {
            writeTx(out + "_htx.csv", 1);
        }
    }

    // -- transmission statistics (KPI window), and the helper checks: every
    //    relayed copy must follow a NACK that a member sent on the previous
    //    transmission of its TB, and a successful decode of the leader's copy by
    //    the helper. relayNoRxNack: no NACK *received* by that transmission's
    //    transmitter (the leader can miss a NACK the helper hears: PSFCH path
    //    loss > MaxLossDb), informational. leaderNackMissed: leader transmissions
    //    NACKed by >= 1 member whose NACKs all missed the leader (Option 1 then
    //    takes the TB as delivered)
    uint64_t lTx = 0, lTxColl = 0, lNew = 0, lRetx = 0, hTx = 0, hTxColl = 0;
    uint64_t relayNoNack = 0, relayNoRxNack = 0, relayNoDecode = 0;
    uint64_t leaderTxNacked = 0, leaderNackMissed = 0, nackSentWin = 0;
    // arm-design checks: leader transmissions per TB (and a retransmission only
    // after a NACK the leader received, Option 1), a helper copy combined into a
    // HARQ process the leader had reused for a newer TB (must be 0), split: a copy
    // before the leader's retransmission, additive: a copy without a NACK sent on
    // the leader's initial transmission (both must be 0)
    uint64_t leaderRetxNoNack = 0, relayReusedHarq = 0, relayUnmatched = 0;
    uint64_t relayBeforeLeaderRetx = 0, relayNoInitialNack = 0;
    std::map<uint32_t, uint32_t> leaderTxOfTb;
    std::map<uint32_t, size_t> lastLeaderTxOfTb; // gen -> u of its latest leader transmission
    for (size_t u = 0; u < utx.size(); ++u)
    {
        const LeaderTx& x = *utx[u].x;
        if (x.tS < kpiStart || x.tS >= kpiEnd)
        {
            continue;
        }
        if (utx[u].side == 0)
        {
            ++lTx;
            lTxColl += nColl[u] > 0 ? 1 : 0;
            nackSentWin += nNackSent[u];
            ++leaderTxOfTb[x.gen];
            auto itl = lastLeaderTxOfTb.find(x.gen);
            if (x.txInTb > 1 && itl != lastLeaderTxOfTb.end() && utx[itl->second].x->nNack == 0)
            {
                ++leaderRetxNoNack;
            }
            lastLeaderTxOfTb[x.gen] = u;
            if (nNackSent[u] > 0)
            {
                ++leaderTxNacked;
                leaderNackMissed += (x.nNack == 0) ? 1 : 0;
            }
            if (x.txInTb == 1)
            {
                ++lNew;
            }
            else
            {
                ++lRetx;
            }
            continue;
        }
        ++hTx;
        hTxColl += nColl[u] > 0 ? 1 : 0;
        nackSentWin += nNackSent[u];
        size_t prev = SIZE_MAX;
        bool decoded = false;
        for (size_t v = 0; v < u; ++v)
        {
            if (utx[v].tb != utx[u].tb)
            {
                continue;
            }
            prev = v;
            if (utx[v].side == 0 && utx[v].idx < g.side[0].phy.size() &&
                g.side[0].phy[utx[v].idx][helper - 1].status == 1)
            {
                decoded = true;
            }
        }
        if (prev == SIZE_MAX || nNackSent[prev] == 0)
        {
            ++relayNoNack;
        }
        if (prev == SIZE_MAX || utx[prev].x->nNack == 0)
        {
            ++relayNoRxNack;
        }
        if (!decoded)
        {
            ++relayNoDecode;
        }
        const int64_t tb = utx[u].tb;
        if (tb < 0 || static_cast<size_t>(tb) >= ltx.size())
        {
            ++relayUnmatched;
        }
        else
        {
            // latest leader transmission on that HARQ id before the copy, and the
            // leader transmissions of this TB before it
            const uint8_t h = ltx[tb].harqId;
            uint32_t ownBefore = 0;
            int64_t lastGenOnHarq = -1;
            for (size_t v = 0; v < u; ++v)
            {
                if (utx[v].side != 0)
                {
                    continue;
                }
                if (utx[v].x->harqId == h)
                {
                    lastGenOnHarq = utx[v].x->gen;
                }
                ownBefore += (utx[v].tb == tb) ? 1 : 0;
            }
            if (lastGenOnHarq != tb)
            {
                ++relayReusedHarq;
            }
            if (helperMode == "split" && ownBefore < 2)
            {
                ++relayBeforeLeaderRetx;
            }
            const size_t u0 = uOf[0][tb];
            if (helperMode == "additive" && (u0 == SIZE_MAX || nNackSent[u0] == 0))
            {
                ++relayNoInitialNack;
            }
        }
    }
    uint32_t leaderTxPerTbMax = 0;
    for (const auto& [gen, n] : leaderTxOfTb)
    {
        leaderTxPerTbMax = std::max(leaderTxPerTbMax, n);
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
              "option1_tx,sl_slot_frac,mcs_rx_min,mcs_rx_max,cbr_occupied,rlc_reordering_ms,"
              "helper_mode,helper,helper_tx,helper_tx_coll_rate,coop_scheduled,coop_sent,"
              "coop_no_nack,coop_too_late,coop_no_harq,coop_no_resource,relay_without_nack,"
              "relay_without_decode,"
              "helper_att,helper_att_ok,helper_recovered,"
              "lcoll_neither,lcoll_helper_only,lcoll_leader_only,lcoll_both,"
              "hcoll_neither,hcoll_helper_only,hcoll_leader_only,hcoll_both,"
              "relay_without_rx_nack,nack_sent,leader_tx_nacked,leader_nack_missed,sel_leader,"
              "sel_helper,sfn_offset_mismatch,sel_leader_cand_init,sel_leader_cand_step5,"
              "sel_leader_cand_final,sel_leader_sensing_excluded,sel_leader_relaxed,"
              "sel_helper_cand_step5,sel_helper_cand_final,sel_helper_sensing_excluded";
        for (const char* pre : {"cc", "ccboth"})
        {
            for (const char* role : {"l", "h"})
            {
                for (int c = 0; c < CC_N; ++c)
                {
                    rc << ',' << pre << '_' << ccName[c] << '_' << role;
                }
            }
        }
        rc << ",relay_budget,leader_tx_per_tb_max,leader_retx_without_nack,"
              "relay_into_reused_harq,relay_unmatched,relay_before_leader_retx,"
              "relay_without_initial_nack,helper_att_after_fail,helper_att_first,div_selections,"
              "coll_cond,leader_grant,sps_reselect_on_misfit,sl_dyn_exclusion,"
              "sl_project_announced,leader_grants_sps,leader_grants_dyn,leader_resel_per_s,"
              "leader_misfit_resel,leader_misfit_enlarged,leader_grant_subch_mean,"
              "leader_grant_bytes_mean,bg_sps_grants_per_s,bg_dyn_grants_per_s,"
              "bg_misfit_resel_per_s,bg_misfit_enlarged_per_s,ack_no_retx_left,"
              "leader_grant_slots_mean,sl_prob_resource_keep,rri_list,leader_kept_per_s,"
              "bg_kept_per_s\n";
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
           << rlcReorderingMs << ',' << helperMode << ',' << helper << ',' << hTx << ','
           << (hTx ? static_cast<double>(hTxColl) / hTx : 0.0) << ',' << g.coopEv[0] << ','
           << g.coopEv[1] << ',' << g.coopEv[2] << ',' << g.coopEv[3] << ',' << g.coopEv[4]
           << ',' << g.coopEv[5] << ',' << relayNoNack << ',' << relayNoDecode << ',' << helperAtt << ','
           << helperAttOk << ',' << helperRecovered;
        for (int r = 0; r < 2; ++r)
        {
            for (int c = 0; c < 4; ++c)
            {
                rc << ',' << collCat[r][c];
            }
        }
        rc << ',' << relayNoRxNack << ',' << nackSentWin << ',' << leaderTxNacked << ','
           << leaderNackMissed << ',' << g.sel[0].size() << ',' << g.sel[1].size() << ','
           << g.sfnOffsetMismatch;
        // mean candidate counts of the selections (S_A, after step 5, after step 6),
        // share of selections where sensing (step 6) removed >= 1 candidate, and
        // where the RSRP threshold had to be relaxed
        for (int o = 0; o < 2; ++o)
        {
            double c0 = 0, c5 = 0, c6 = 0, excl = 0, relax = 0;
            for (const SelEvent& e : g.sel[o])
            {
                c0 += e.nCandInit;
                c5 += e.nCandStep5;
                c6 += e.nCandFinal;
                excl += (e.nCandFinal < e.nCandStep5) ? 1 : 0;
                relax += (e.thrFinal > e.thrInit) ? 1 : 0;
            }
            const double n = std::max<double>(1.0, g.sel[o].size());
            if (o == 0)
            {
                rc << ',' << c0 / n << ',' << c5 / n << ',' << c6 / n << ',' << excl / n << ','
                   << relax / n;
            }
            else
            {
                rc << ',' << c5 / n << ',' << c6 / n << ',' << excl / n;
            }
        }
        for (const auto* cc : {&ccAll, &ccBoth})
        {
            for (int r = 0; r < 2; ++r)
            {
                for (int c = 0; c < CC_N; ++c)
                {
                    rc << ',' << (*cc)[r][c];
                }
            }
        }
        rc << ',' << (helperMode == "additive" ? relayBudget : 0) << ',' << leaderTxPerTbMax << ','
           << leaderRetxNoNack << ',' << relayReusedHarq << ',' << relayUnmatched << ','
           << relayBeforeLeaderRetx << ',' << relayNoInitialNack << ',' << helperAttAfterFail
           << ',' << helperAttFirst << ',' << g.div.size() << ',' << collCond << ','
           << leaderGrant << ',' << spsReselectOnMisfit << ',' << slDynExclusion << ','
           << slProjectAnnounced << ',' << gAgg[0].sps << ',' << gAgg[0].dyn << ','
           << gAgg[0].sps / winS << ',' << gAgg[0].misfitResel << ',' << gAgg[0].misfitEnlarged
           << ',' << (gAgg[0].sps + gAgg[0].dyn ? gAgg[0].subchSum / (gAgg[0].sps + gAgg[0].dyn) : 0.0)
           << ',' << (gAgg[0].sps + gAgg[0].dyn ? gAgg[0].bytesSum / (gAgg[0].sps + gAgg[0].dyn) : 0.0)
           << ',' << gAgg[1].sps / winS << ',' << gAgg[1].dyn / winS << ','
           << gAgg[1].misfitResel / winS << ',' << gAgg[1].misfitEnlarged / winS << ','
           << ctl.nAckNoRetxLeft << ','
           << (gAgg[0].sps + gAgg[0].dyn ? gAgg[0].slotsSum / (gAgg[0].sps + gAgg[0].dyn) : 0.0)
           << ',' << slProbResourceKeep << ',' << (rriListStr.empty() ? "default" : rriListCsv)
           << ',' << gAgg[0].kept / winS << ',' << gAgg[1].kept / winS << '\n';
    }

    std::cerr << "  pkts=" << pktInWin << " notTx=" << pktNotTx << " leaderTx=" << lTx
              << " (new " << lNew << ", retx " << lRetx << ") collTx=" << lTxColl
              << " CBR=" << std::setprecision(3) << cbr << " PRR=" << prrMean << " (min "
              << prrMin << ") coll=" << collMean << " chan=" << chanMean << " notx=" << notxMean << " other=" << otherMean
              << " NACK=" << ctl.nNackRx << " implicitACK=" << ctl.nImplicitAck
              << " stale=" << ctl.nStale << " misaligned=" << ctl.nSlotMisaligned
              << " unmatched=" << g.phyUnmatched << " wall=" << std::setprecision(1) << wall
              << "s\n";
    std::cerr << "  RNTIs ready at " << rntiReadyAt << " s\n";
    std::cerr << "  grants: leader " << leaderGrant << " (sps " << gAgg[0].sps << ", dynamic "
              << gAgg[0].dyn << ", misfit reselections " << gAgg[0].misfitResel << ", enlarged "
              << gAgg[0].misfitEnlarged << ", implicit ACKs with no retx left "
              << ctl.nAckNoRetxLeft << "); background sps " << gAgg[1].sps << " dynamic "
              << gAgg[1].dyn << " misfit reselections " << gAgg[1].misfitResel << "\n";
    if (g.numSides > 1)
    {
        std::cerr << "  helper " << helper << ": relay tx=" << hTx << " (coll " << hTxColl
                  << ") scheduled=" << g.coopEv[0] << " sent=" << g.coopEv[1]
                  << " noNack=" << g.coopEv[2] << " tooLate=" << g.coopEv[3]
                  << " noHarq=" << g.coopEv[4] << " noResource=" << g.coopEv[5] << " | checks: relay w/o NACK=" << relayNoNack
                  << " relay w/o decode=" << relayNoDecode << " | member attempts "
                  << helperAtt << " ok " << helperAttOk << " recovered " << helperRecovered
                  << " | relay w/o NACK received by the transmitter=" << relayNoRxNack << "\n";
    }
    std::cerr << "  checks: leader tx/TB max " << leaderTxPerTbMax << ", leader retx w/o NACK "
              << leaderRetxNoNack << ", relay into reused HARQ " << relayReusedHarq
              << ", relay unmatched " << relayUnmatched << ", split relay before leader retx "
              << relayBeforeLeaderRetx << ", additive relay w/o NACK on initial "
              << relayNoInitialNack << " | helper copies at members: after a failed attempt "
              << helperAttAfterFail << ", first attempt " << helperAttFirst << "\n";
    std::cerr << "  NACKs sent " << nackSentWin << "; leader tx NACKed " << leaderTxNacked
              << ", all NACKs missed by the leader " << leaderNackMissed << " | selections leader "
              << g.sel[0].size() << " helper " << g.sel[1].size() << " | slot offset "
              << g.sfnOffset << " mismatches " << g.sfnOffsetMismatch << "\n";
    for (int r = 0; r < g.numSides; ++r)
    {
        std::cerr << "  collided (attempt, interferer) pairs, " << (r ? "helper" : "leader") << ":";
        for (int c = 0; c < CC_N; ++c)
        {
            if (ccAll[r][c])
            {
                std::cerr << ' ' << ccName[c] << '=' << ccAll[r][c] << " (both " << ccBoth[r][c]
                          << ')';
            }
        }
        std::cerr << "\n";
    }

    Simulator::Destroy();
    return 0;
}
