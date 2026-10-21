// G01_blind_effectiveness (C: CORE; W: SYN_MARGINAL, SYN_NEAR; arms R1, R4_S1_H2): how many blind retransmissions help?  Every attempt
// k >= 2 of a TB is classified per member as effective / redundant / futile; per attempt as wasted / useful / pure futile; the worst
// receivers (tail = member 8, empirical worst = lowest PRR_pdb in R1) are followed explicitly.
#include "common/analysis_kit.h"
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <array>
#include <cmath>
#include <map>

using namespace ptest;

namespace
{
struct Cnt
{
    long attempts{0}, wasted{0}, useful{0}, pureFutile{0};
    long eff{0}, red{0}, fut{0}, futSci{0}, futColl{0}, futChan{0}; // per attempt x member (self excluded)
    long needed{0};                                                   // eff + fut
};
} // namespace

int
main()
{
    TestReport T("G01", "blind_effectiveness", "G");
    T.Summary("Q7: of the blind retransmissions after the first transmission, which ones help?  Per attempt index and member: effective "
              "(delivers a TB the member lacked), redundant (the member already holds it) or futile (still fails); worst receivers followed explicitly.");
    T.AddRef("TEST_PLAN G01; README (blind retransmission, Kuo helper); TS 38.214 8.1.4 [V]");
    ViolationLog V;
    CsvWriter ineff(TestsRoot() + "/results/G01_ineffective_retx_worst.csv", "arm,cell,seed,tb,attempt_idx,side,tx_node,member,worst_def,dist_m,status,sinr_db,cond,n_coll,reason");
    CsvWriter tab(TestsRoot() + "/results/G01_attempt_table.csv", "runset,arm,cell,attempt_idx,attempts,wasted,useful,pure_futile,eff,red,fut,fut_sci,fut_coll,fut_channel");
    CsvWriter memb(TestsRoot() + "/results/G01_member_table.csv", "runset,arm,cell,attempt_idx,member,effective,redundant,futile,p_effective_given_needed");
    T.Artefact("results/G01_ineffective_retx_worst.csv");
    T.Artefact("results/G01_attempt_table.csv");
    T.Artefact("results/G01_member_table.csv");

    for (const std::string rs : {"SYN_NEAR", "SYN_MARGINAL", "CORE"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { if (rs == "CORE") T.Skip("CORE data", "CORE manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        // cell -> arm -> attempt idx -> Cnt ; member tables
        std::map<std::string, std::map<std::string, std::map<int, Cnt>>> cnt;
        std::map<std::string, std::map<std::string, std::map<int, std::array<std::array<long, 3>, kMembers>>>> mt; // eff/red/fut per member
        std::map<std::string, std::map<std::string, std::map<int, std::array<long, 2>>>> worstEff; // cell -> arm -> {eff, needed} for the empirical worst member
        std::map<std::string, std::map<std::string, std::map<int, std::array<long, 2>>>> tailEff;
        std::map<std::string, std::vector<double>> r4Fewer; // split of R4 TBs with fewer than 2 copies
        long r4TbFew = 0, r4HelperND = 0, r4Other = 0, r4Tbs = 0;
        long identityBad = 0;
        // worst member per (cell, seed) from R1
        std::map<std::string, int> worstOfCellSeed;
        for (const auto& rr : Select(all, "R1"))
        {
            Run r;
            if (!r.Load(rr)) continue;
            worstOfCellSeed[rr.CellKey() + "/s" + std::to_string(rr.seed)] = WorstMemberByPrr(r);
        }
        for (const auto& rr : all)
        {
            if (rr.arm != "R1" && rr.arm != "R4_S1_H2") continue;
            Run r;
            if (!r.Load(rr) || !r.hasAtt) continue;
            T.AddNRuns(1);
            const std::string cell = rr.CellKey();
            const std::string cs = cell + "/s" + std::to_string(rr.seed);
            const int worst = worstOfCellSeed.count(cs) ? worstOfCellSeed[cs] : kMembers;
            for (const TbView& tv : WindowTbViews(r))
            {
                if (rr.arm == "R4_S1_H2")
                {
                    ++r4Tbs;
                    if (tv.helper.size() < 2)
                    {
                        ++r4TbFew;
                        const int h = tv.helperNode;
                        const bool dec = h >= 1 && h <= kMembers && tv.initial->status[h - 1] == 1;
                        if (!dec) ++r4HelperND; else ++r4Other;
                    }
                }
                for (size_t k = 1; k < tv.all.size(); ++k)
                {
                    const int idx = static_cast<int>(k) + 1;
                    Cnt& c = cnt[cell][rr.arm][idx];
                    ++c.attempts;
                    bool anyEff = false, allRed = true;
                    int nonSelf = 0;
                    for (int m = 1; m <= kMembers; ++m)
                    {
                        const Eff e = ClassifyAttempt(r, tv, k, m);
                        if (e == Eff::Self) continue;
                        ++nonSelf;
                        auto& cm = mt[cell][rr.arm][idx][m - 1];
                        if (e == Eff::Effective) { ++c.eff; ++cm[0]; anyEff = true; allRed = false; }
                        else if (e == Eff::Redundant) { ++c.red; ++cm[1]; }
                        else
                        {
                            ++c.fut; ++cm[2]; allRed = false;
                            (e == Eff::FutileSci ? c.futSci : e == Eff::FutileColl ? c.futColl : c.futChan)++;
                        }
                        if (e == Eff::Effective || e == Eff::FutileSci || e == Eff::FutileColl || e == Eff::FutileChannel)
                        {
                            if (m == worst) { worstEff[cell][rr.arm][idx][1]++; worstEff[cell][rr.arm][idx][0] += (e == Eff::Effective); }
                            if (m == kMembers) { tailEff[cell][rr.arm][idx][1]++; tailEff[cell][rr.arm][idx][0] += (e == Eff::Effective); }
                        }
                        if ((m == worst || m == kMembers) && (e == Eff::FutileSci || e == Eff::FutileColl || e == Eff::FutileChannel))
                        {
                            const PTx& x = *tv.all[k];
                            const std::string reason = e == Eff::FutileSci ? "sci_missed" : e == Eff::FutileColl ? "corrupt_collision" : "corrupt_channel";
                            std::string wd = (m == worst ? "empirical_worst" : "") ;
                            if (m == kMembers) wd += std::string(wd.empty() ? "" : "+") + "tail";
                            ineff.Row({rr.arm, cell, std::to_string(rr.seed), FmtI(tv.id), std::to_string(idx), std::to_string(x.side), std::to_string(x.node), std::to_string(m), wd,
                                       Fmt(x.dist[m - 1], 5), std::to_string(x.status[m - 1]), Fmt(x.sinr[m - 1], 4), std::to_string(x.cond[m - 1]), std::to_string(x.nColl), reason});
                        }
                    }
                    if (anyEff) ++c.useful;
                    else if (allRed && nonSelf > 0) ++c.wasted;
                    else ++c.pureFutile;
                }
            }
        }
        if (cnt.empty()) continue;
        // identity: effective + redundant + futile per attempt-member == attempts x non-self members (by construction: re-count independently)
        for (const auto& [cell, byArm] : cnt)
            for (const auto& [arm, byIdx] : byArm)
                for (const auto& [idx, c] : byIdx)
                {
                    const long members = c.eff + c.red + c.fut;
                    // an attempt by the helper has 7 receivers, a leader attempt 8
                    if (members < 7 * c.attempts || members > 8 * c.attempts || c.wasted + c.useful + c.pureFutile != c.attempts) ++identityBad;
                    tab.Row({rs, arm, cell, std::to_string(idx), FmtI(c.attempts), FmtI(c.wasted), FmtI(c.useful), FmtI(c.pureFutile), FmtI(c.eff), FmtI(c.red), FmtI(c.fut), FmtI(c.futSci), FmtI(c.futColl), FmtI(c.futChan)});
                }
        for (const auto& [cell, byArm] : mt)
            for (const auto& [arm, byIdx] : byArm)
                for (const auto& [idx, arr] : byIdx)
                    for (int m = 0; m < kMembers; ++m)
                    {
                        const long needed = arr[m][0] + arr[m][2];
                        memb.Row({rs, arm, cell, std::to_string(idx), std::to_string(m + 1), FmtI(arr[m][0]), FmtI(arr[m][1]), FmtI(arr[m][2]), Fmt(needed ? static_cast<double>(arr[m][0]) / needed : NAN, 5)});
                    }
        T.Check(rs + ": effective + redundant + futile = receivers x attempts, and wasted + useful + pure futile = attempts", identityBad == 0, Opt().Exp("0").Obs(FmtI(identityBad)).Tol("0").Sev("M"));
        // aggregated shares
        std::map<std::string, std::map<int, Cnt>> agg; // arm -> idx
        for (const auto& [cell, byArm] : cnt) for (const auto& [arm, byIdx] : byArm) for (const auto& [idx, c] : byIdx)
        {
            Cnt& a = agg[arm][idx];
            a.attempts += c.attempts; a.wasted += c.wasted; a.useful += c.useful; a.pureFutile += c.pureFutile; a.eff += c.eff; a.red += c.red; a.fut += c.fut;
        }
        for (const auto& [arm, byIdx] : agg)
            for (const auto& [idx, a] : byIdx)
            {
                if (a.attempts == 0) continue;
                const Prop pw = Wilson(a.wasted, a.attempts), pu = Wilson(a.useful, a.attempts), pf = Wilson(a.pureFutile, a.attempts);
                T.Info(rs + " " + arm + " attempt " + std::to_string(idx) + " (n=" + FmtI(a.attempts) + ")",
                       Opt().Obs("wasted " + Fmt(pw.p, 3) + " [" + Fmt(pw.lo, 3) + "," + Fmt(pw.hi, 3) + "], useful " + Fmt(pu.p, 3) + " [" + Fmt(pu.lo, 3) + "," + Fmt(pu.hi, 3) + "], pure futile " +
                                 Fmt(pf.p, 3) + " [" + Fmt(pf.lo, 3) + "," + Fmt(pf.hi, 3) + "]").Note("share of attempts: wasted = redundant for every receiver (an oracle HARQ would skip it)"));
                T.Metric(rs + "_" + arm + "_a" + std::to_string(idx) + "_wasted", pw.p);
                T.Metric(rs + "_" + arm + "_a" + std::to_string(idx) + "_useful", pu.p);
                T.Metric(rs + "_" + arm + "_a" + std::to_string(idx) + "_futile", pf.p);
            }
        // lossless cells: wasted share ~ 100%
        if (rs == "SYN_NEAR")
            for (const auto& [arm, byIdx] : agg)
                for (const auto& [idx, a] : byIdx)
                    if (a.attempts > 50)
                    {
                        const double sh = static_cast<double>(a.wasted) / a.attempts;
                        T.Check(rs + " " + arm + " attempt " + std::to_string(idx) + ": wasted share >= 0.99 in the no-loss cell", sh >= 0.99, Opt().Exp(">= 0.99").Obs(Fmt(sh, 4) + " of " + FmtI(a.attempts)).Sev("M"));
                    }
        // worst receivers: P(effective | needed) R4 vs R1
        for (const std::string& defn : {std::string("tail (member 8)"), std::string("empirical worst (lowest PRR_pdb in R1)")})
        {
            const auto& W = (defn[0] == 't') ? tailEff : worstEff;
            for (int idx : {2, 3})
            {
                std::vector<double> a, b;
                long e1 = 0, n1 = 0, e4 = 0, n4 = 0;
                for (const auto& [cell, byArm] : W)
                {
                    if (!byArm.count("R1") || !byArm.count("R4_S1_H2")) continue;
                    const auto i1 = byArm.at("R1").find(idx), i4 = byArm.at("R4_S1_H2").find(idx);
                    if (i1 == byArm.at("R1").end() || i4 == byArm.at("R4_S1_H2").end()) continue;
                    e1 += i1->second[0]; n1 += i1->second[1]; e4 += i4->second[0]; n4 += i4->second[1];
                    if (i1->second[1] > 0 && i4->second[1] > 0)
                    {
                        a.push_back(static_cast<double>(i4->second[0]) / i4->second[1]);
                        b.push_back(static_cast<double>(i1->second[0]) / i1->second[1]);
                    }
                }
                if (n1 == 0 && n4 == 0) continue;
                const Prop p1 = Wilson(e1, n1), p4 = Wilson(e4, n4);
                const DiffCI d = Paired(a, b);
                T.Info(rs + " worst receiver, " + defn + ", attempt " + std::to_string(idx) + ": P(effective | needed) R1 vs R4",
                       Opt().Obs("R1 " + Fmt(p1.p, 4) + " [" + Fmt(p1.lo, 4) + "," + Fmt(p1.hi, 4) + "] (n=" + FmtI(n1) + "), R4 " + Fmt(p4.p, 4) + " [" + Fmt(p4.lo, 4) + "," + Fmt(p4.hi, 4) + "] (n=" + FmtI(n4) + "); paired R4-R1 over cells " +
                                 (d.ok() ? FmtCi(d.diff, d.lo, d.hi, 4) : Fmt(d.diff, 4)) + " (n=" + FmtI(static_cast<long>(a.size())) + ")").Note("INFO per section 7.3; R4 attempts are helper copies, R1 attempts are leader copies"));
            }
        }
        if (r4Tbs > 0)
            T.Info(rs + " R4: TBs with fewer than 2 helper copies (F18 split)",
                   Opt().Fnd("F18").Obs(FmtI(r4TbFew) + " of " + FmtI(r4Tbs) + " TBs (" + Fmt(100.0 * r4TbFew / r4Tbs, 3) + " %): helper did not decode the initial transmission " + FmtI(r4HelperND) + ", other (coop_too_late / no resource) " + FmtI(r4Other))
                       .Note("settles F18: the missing copies are explained by helper decode failures; 'other' would be a scheduling shortfall"));
    }
    const std::string art = V.Write("G01_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
