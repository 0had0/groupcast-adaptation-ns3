// G08_silent_helper_gap (C: CORE + SPSDYN, R6_S1_H2; W: SYN_MARGINAL, SYN_DTX): the Option-2 silent-helper gap (decision D-5): a helper that missed
// the SCI is silent, the leader reads the silence as 'helper has it', and the helper's own packet (H0a) or the relay for the other members (H0b) is
// lost.  Counts, probabilities, conditions, PRR impact, the user's protocol requirement (fallback retransmission when nothing was sensed in the
// handed slot), the feasibility upper bound and the ambiguity analysis.
#include "common/analysis_kit.h"
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>

using namespace ptest;

namespace
{
struct Tally { long n{0}, h0{0}, h0a{0}, h0b{0}, h2c{0}; };
const char* CondName(int c) { return c == 2 ? "LOS" : c == 1 ? "NLOSv" : c == 0 ? "NLOS" : "unknown"; }
std::string
DistBin(double d)
{
    return d <= 50 ? "<=50m" : d <= 100 ? "50-100m" : d <= 200 ? "100-200m" : d <= 300 ? "200-300m" : ">300m";
}
} // namespace

int
main()
{
    TestReport T("G08", "silent_helper_gap", "G");
    T.Summary("D-5: how often does the Option-2 silent helper miss the SCI (H0), what happens then (H0a helper's packet lost, H0b relay lost), under "
              "which conditions, and does the leader retransmit when nothing is sensed in the handed slot (the user's protocol requirement)?");
    T.AddRef("README blind spot of the silent helper (~L1419-1443); TEST_PLAN D-5, F07, F08");
    T.AddRef("User requirement (D-5): if the helper missed the TB, the leader senses the retransmission slot allocated to the helper and, if nothing was sensed, retransmits afterwards if possible");
    ViolationLog V;
    CsvWriter cond(TestsRoot() + "/results/G08_conditions.csv", "runset,dimension,value,tbs,h0,h0a,h0b,h2c,p_h0,p_h0_lo,p_h0_hi,p_h0b,p_h0b_lo,p_h0b_hi");
    CsvWriter tbl(TestsRoot() + "/results/G08_events.csv", "runset,cell,seeds,tbs,h0,h0a,h0b,h2c,h0b_leader_retx,h0b_feasible,h0b_nothing_sensed,allack_empty_slots,gain_upper_bound_prr");
    T.Artefact("results/G08_conditions.csv");
    T.Artefact("results/G08_events.csv");

    for (const std::string rs : {"CORE", "SPSDYN", "SYN_MARGINAL", "SYN_DTX"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { if (rs == "CORE" || rs == "SPSDYN") T.Skip(rs + " data", rs + " manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        std::map<std::string, Tally> dims;                   // "dimension|value"
        std::map<std::string, std::array<long, 8>> perCell;  // cell -> counters
        long runs = 0, tbs = 0, h0 = 0, h0a = 0, h0b = 0, h2c = 0, h0bRetx = 0, h0bFeasible = 0, h0bNothing = 0, allAckEmpty = 0, h0bNeeders = 0, h0bNeedersDelivered = 0, ctrlNeeders = 0, ctrlDelivered = 0;
        double gainUb = 0, memberPackets = 0;
        std::vector<double> helperPrr, worstOther;
        for (const auto& rr : Select(all, "R6_S1_H2"))
        {
            Run r;
            if (!r.Load(rr) || !r.hasAtt || !r.hasAlltx || !r.hasHsel) continue;
            ++runs;
            T.AddNRuns(1);
            std::vector<PairOutcome> po;
            ClassifyPairs(r, po);
            std::map<std::pair<int64_t, int>, bool> deliv;
            for (const auto& p : po) deliv[{p.tb, p.member}] = p.delivered();
            // typical offset of the last handed slot: median offset of the second helper copy
            std::vector<double> off2;
            std::vector<TbView> tvs = WindowTbViews(r);
            for (const TbView& tv : tvs) if (tv.helper.size() >= 2) off2.push_back((tv.helper[1]->slot - tv.initial->slot) * 1.0);
            const double lastHandedOffsetSlots = off2.empty() ? 17.0 : Median(off2);
            // empirical P(member decodes a leader initial transmission)
            std::vector<double> pDec(kMembers, 0.0), pN(kMembers, 0.0);
            for (const TbView& tv : tvs)
                for (int m = 1; m <= kMembers; ++m)
                    if (tv.initial->status[m - 1] == 1 || tv.initial->status[m - 1] == 2) { pN[m - 1] += 1; pDec[m - 1] += (tv.initial->status[m - 1] == 1); }
            // helper node transmissions (alltx) for the "nothing sensed" proxy
            std::map<int, std::set<int64_t>> nodeSlots;
            {
                const auto& node = r.alltxT.D("node");
                const auto& dst = r.alltxT.D("dst");
                const auto& slot = r.alltxT.D("slot_abs");
                for (size_t i = 0; i < r.alltxT.nrows; ++i) if (dst[i] == 254 && node[i] != 0) nodeSlots[static_cast<int>(node[i])].insert(static_cast<int64_t>(slot[i]));
            }
            long cellH0 = 0, cellH0a = 0, cellH0b = 0, cellH2c = 0, cellTbs = 0, cellRetx = 0, cellFeas = 0, cellNothing = 0, cellAllAck = 0;
            for (const TbView& tv : tvs)
            {
                ++tbs; ++cellTbs;
                const int h = tv.helperNode;
                if (h < 1 || h > kMembers) continue;
                const int st = tv.initial->status[h - 1];
                bool nonHelperNeed = false;
                for (int m = 1; m <= kMembers; ++m) if (m != h && tv.initial->status[m - 1] != 1) nonHelperNeed = true;
                const bool isH0 = (st == 0), isH2c = (st == 2);
                bool decided = false;
                for (const PTx* x : tv.leader) decided = decided || x->decision == 1;
                const bool isH0a = isH0 && !nonHelperNeed && tv.initial->decision == 1;
                const bool isH0b = isH0 && nonHelperNeed;
                h0 += isH0; h0a += isH0a; h0b += isH0b; h2c += isH2c;
                cellH0 += isH0; cellH0a += isH0a; cellH0b += isH0b; cellH2c += isH2c;
                // all-ACK TBs with empty handed slots (relay suppressed): helper decoded and nobody needs it
                if (st == 1 && !nonHelperNeed && tv.initial->decision == 1 && tv.helper.empty()) { ++allAckEmpty; ++cellAllAck; }
                // conditions (dimensions)
                const int cnd = tv.initial->cond[h - 1];
                const std::vector<std::pair<std::string, std::string>> dimv = {
                    {"scenario", rr.scenario}, {"ivd", rr.ivd}, {"traffic", rr.traffic}, {"background", rr.bg}, {"link_condition", CondName(cnd)},
                    {"helper_distance", DistBin(tv.initial->dist[h - 1])}, {"initial_collision", tv.initial->nColl > 0 ? "yes" : "no"}, {"helper_index", std::to_string(h)},
                    {"sinr_bin_of_helper_link", (st == 0 || std::isnan(tv.initial->sinr[h - 1]) || tv.initial->sinr[h - 1] < -90) ? "no_sinr_sci_missed" : (tv.initial->sinr[h - 1] < 10 ? "<10dB" : tv.initial->sinr[h - 1] < 20 ? "10-20dB" : ">=20dB")}};
                for (const auto& [d, v] : dimv)
                {
                    Tally& t = dims[d + "|" + v];
                    ++t.n; t.h0 += isH0; t.h0a += isH0a; t.h0b += isH0b; t.h2c += isH2c;
                }
                if (!isH0b) continue;
                // requirement: a leader retransmission after the empty handed slots, within the PDB and the budget
                bool retx = false;
                for (const PTx* x : tv.leader) retx = retx || (x->txInTb > 1 && x->slot >= tv.initial->slot + 5);
                h0bRetx += retx; cellRetx += retx;
                // feasibility: a slot after the last handed slot with remaining PDB >= T1 + 1 slots and budget left
                const double tLast = tv.initial->t + lastHandedOffsetSlots * kSlotS;
                const bool pdbOk = !std::isnan(tv.tGen) && (tv.tGen + r.pdbS - tLast) >= 3 * kSlotS;
                const bool budget = static_cast<int>(tv.all.size()) < r.maxNumTx;
                const bool feasible = pdbOk && budget;
                h0bFeasible += feasible; cellFeas += feasible;
                // nothing sensed in the handed slots (proxy: the helper transmitted nothing within 31 slots)
                bool sensed = false;
                for (const auto& sl : nodeSlots[h]) if (sl > tv.initial->slot && sl <= tv.initial->slot + 31) { sensed = true; break; }
                h0bNothing += !sensed; cellNothing += !sensed;
                // PRR impact and gain upper bound
                for (int m = 1; m <= kMembers; ++m)
                {
                    if (m == h || tv.initial->status[m - 1] == 1) continue;
                    ++h0bNeeders;
                    auto it = deliv.find({tv.id, m});
                    if (it != deliv.end() && it->second) ++h0bNeedersDelivered;
                    if (feasible && pN[m - 1] > 0) gainUb += pDec[m - 1] / pN[m - 1] * (it != deliv.end() && !it->second ? 1.0 : 0.0);
                }
            }
            for (const auto& p : po) memberPackets += 1;
            // control: members who needed the relay in TBs where the helper DID decode
            for (const TbView& tv : tvs)
            {
                const int h = tv.helperNode;
                if (h < 1 || h > kMembers || tv.initial->status[h - 1] != 1) continue;
                for (int m = 1; m <= kMembers; ++m)
                {
                    if (m == h || tv.initial->status[m - 1] == 1) continue;
                    ++ctrlNeeders;
                    auto it = deliv.find({tv.id, m});
                    ctrlDelivered += (it != deliv.end() && it->second);
                }
            }
            perCell[rr.CellKey()] = {cellTbs, cellH0, cellH0a, cellH0b, cellH2c, cellRetx, cellFeas, cellNothing};
            tbl.Row({rs, rr.CellKey() + "/s" + std::to_string(rr.seed), "1", FmtI(cellTbs), FmtI(cellH0), FmtI(cellH0a), FmtI(cellH0b), FmtI(cellH2c), FmtI(cellRetx), FmtI(cellFeas), FmtI(cellNothing), FmtI(cellAllAck), ""});
            // helper PRR vs worst other
            std::map<int, int> cnt;
            for (const TbView& tv : tvs) cnt[tv.helperNode]++;
            int hm = 0, best = 0;
            for (const auto& [hh, n] : cnt) if (n > best) { best = n; hm = hh; }
            if (hm >= 1 && hm <= kMembers)
            {
                helperPrr.push_back(r.MemberCol("prr_pdb", hm));
                double w = 2; for (int m = 1; m <= kMembers; ++m) if (m != hm) w = std::min(w, r.MemberCol("prr_pdb", m));
                worstOther.push_back(w);
            }
        }
        if (runs == 0 || tbs == 0) continue;
        const std::string pre = rs + " R6: ";
        const Prop p0 = Wilson(h0, tbs), pa = Wilson(h0a, tbs), pb = Wilson(h0b, tbs), pc = Wilson(h2c, tbs);
        T.Info(pre + "events per TB named to a helper (H0 SCI missed, H0a lost helper packet, H0b lost relay, H2c implemented NACK-reclaim path)",
               Opt().Fnd("F07").Obs("H0 " + FmtCi(p0.p, p0.lo, p0.hi, 4) + " (" + FmtI(h0) + "), H0a " + FmtCi(pa.p, pa.lo, pa.hi, 4) + " (" + FmtI(h0a) + "), H0b " + FmtCi(pb.p, pb.lo, pb.hi, 4) + " (" + FmtI(h0b) + "), H2c " + FmtCi(pc.p, pc.lo, pc.hi, 4) + " (" + FmtI(h2c) + ") of " + FmtI(tbs) + " TBs in " + FmtI(runs) + " runs")
                   .Note("unit: TB; the helper's status on the leader's initial transmission comes from _att (member == applied helper)"));
        T.Metric(rs + "_tbs", static_cast<double>(tbs));
        T.Metric(rs + "_h0", static_cast<double>(h0));
        T.Metric(rs + "_h0a", static_cast<double>(h0a));
        T.Metric(rs + "_h0b", static_cast<double>(h0b));
        T.Metric(rs + "_h2c", static_cast<double>(h2c));
        T.Metric(rs + "_h0_p", p0.p);
        T.Metric(rs + "_h0b_p", pb.p);
        // conditions: top-5 cells by H0 probability (n >= 30)
        std::vector<std::pair<double, std::string>> ranked;
        for (const auto& [k, t] : dims)
        {
            const auto bar = k.find('|');
            const Prop a = Wilson(t.h0, t.n), b = Wilson(t.h0b, t.n);
            cond.Row({rs, k.substr(0, bar), k.substr(bar + 1), FmtI(t.n), FmtI(t.h0), FmtI(t.h0a), FmtI(t.h0b), FmtI(t.h2c), Fmt(a.p, 6), Fmt(a.lo, 6), Fmt(a.hi, 6), Fmt(b.p, 6), Fmt(b.lo, 6), Fmt(b.hi, 6)});
            if (t.n >= 30) ranked.push_back({a.p, k + " n=" + FmtI(t.n) + " P(H0)=" + Fmt(a.p, 3)});
        }
        std::sort(ranked.rbegin(), ranked.rend());
        std::string top;
        for (size_t i = 0; i < ranked.size() && i < 5; ++i) top += ranked[i].second + "; ";
        T.Info(pre + "conditions with the highest P(H0), top 5 (n >= 30 TBs)", Opt().Obs(top.empty() ? "no condition with 30 TBs" : top).Note("full table: results/G08_conditions.csv"));
        // PRR impact
        const MeanCI mh = MeanCi(helperPrr), mo = MeanCi(worstOther);
        const Prop needD = Wilson(h0bNeedersDelivered, h0bNeeders), ctrlD = Wilson(ctrlDelivered, ctrlNeeders);
        T.Info(pre + "PRR impact: helper's own PRR_pdb vs the worst other member; delivery of members that needed a relay (H0b) vs the same need when the helper decoded",
               Opt().Obs("helper " + (mh.ok() ? FmtCi(mh.mean, mh.lo, mh.hi, 4) : Fmt(mh.mean, 4)) + " vs worst other " + (mo.ok() ? FmtCi(mo.mean, mo.lo, mo.hi, 4) : Fmt(mo.mean, 4)) +
                         "; members needing a relay delivered: H0b " + Fmt(needD.p, 4) + " (n=" + FmtI(h0bNeeders) + "), helper decoded " + Fmt(ctrlD.p, 4) + " (n=" + FmtI(ctrlNeeders) + ")"));
        // protocol requirement check
        if (h0b > 0)
        {
            const Prop cov = Wilson(h0bRetx, h0b);
            if (h0bRetx == h0b)
                T.Pass(pre + "user requirement: after an empty handed slot the leader retransmits (within the PDB and the budget)", Opt().Exp("leader retransmission for every H0b TB").Obs(FmtI(h0bRetx) + " of " + FmtI(h0b)));
            else
                T.Fail(pre + "user requirement: if the helper missed the TB, the leader senses the handed slot and, if nothing was sensed, retransmits afterwards if possible", Opt().Sev("H")
                           .Exp("a leader retransmission after the empty handed slot for every H0b TB where feasible")
                           .Obs("leader retransmitted in " + FmtI(h0bRetx) + " of " + FmtI(h0b) + " H0b TBs (coverage " + Fmt(cov.p, 4) + " [" + Fmt(cov.lo, 4) + "," + Fmt(cov.hi, 4) + "]); a retransmission would have been feasible (PDB and budget) for " + FmtI(h0bFeasible) + "; nothing from the helper in its handed slots (sensing proxy) for " + FmtI(h0bNothing))
                           .Note("the code reclaims a handed slot only on the helper's NACK (nack_only_harq.cc:392-415); a helper that missed the SCI never NACKs. The leader's own PSCCH decoding is not logged: 'nothing sensed' is proxied by _alltx (no PSSCH of the helper in the slots after the initial transmission)."));
            T.Info(pre + "feasibility / benefit upper bound of the fallback", Opt().Obs(FmtI(h0bFeasible) + " of " + FmtI(h0b) + " H0b TBs could have been served (remaining PDB >= T1 + 1 slot after the last handed slot, budget left); PRR gain upper bound " +
                                                                                 Fmt(memberPackets > 0 ? gainUb / memberPackets : 0.0, 5) + " (member-packets recovered / all member-packets, each assumed to decode with the member's empirical single-transmission success rate)")
                       .Note("the last handed slot is assumed at the median offset of the second helper copy of the same run"));
        }
        else
            T.Info(pre + "user requirement check", Opt().Obs("no H0b TB in this run-set"));
        // ambiguity
        T.Info(pre + "ambiguity of 'nothing sensed' (orchestrator's argument): the unconditioned fallback rule would retransmit after every all-ACK TB, the conditioned rule only after H0b",
               Opt().Obs("spurious fallbacks under the unconditioned rule (all-ACK TBs with empty handed slots): " + FmtI(allAckEmpty) + " TBs; useful fallbacks under the conditioned rule (H0b): " + FmtI(h0b) + " TBs; ratio spurious : useful = " + (h0b > 0 ? Fmt(static_cast<double>(allAckEmpty) / h0b, 4) : std::string("inf")))
                   .Note("when every other member ACKed, R6 suppresses the relay on purpose, so an empty handed slot is expected even if the helper decoded; a fallback must therefore fire only when the leader saw a NACK or DTX from a non-helper member for that TB"));
    }
    const std::string art = V.Write("G08_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
