// G05_helper_reliability_worst_rx (C: CORE R4/R5/R6 with the Kuo helper; W: SYN_DIST): how reliable is the selected helper at raising the PRR of the
// worst receiver?  P_hd, P(recovered | needed), hindsight-best helper, PRR_worst helper arm minus leader-only counterpart, selector calibration, F28.
#include "common/analysis_kit.h"
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <cmath>
#include <map>

using namespace ptest;

int
main()
{
    TestReport T("G05", "helper_reliability_worst_rx", "G");
    T.Summary("Q9: the Kuo helper decodes the initial transmission in P_hd of the TBs and recovers the worst receiver in P(recovered | needed); the "
              "helper arm never lowers the worst receiver's PRR; run.csv helper_drop_rate / relay_needed are mis-defined (F28).");
    T.AddRef("TEST_PLAN G05; Kuo et al. 2021 (GPRR); README (helper metrics)");
    ViolationLog V;
    CsvWriter calib(TestsRoot() + "/results/G05_selector_calibration.csv", "runset,arm,scenario,ivd,bg,traffic,seed,predicted_score_mean,measured_prr_mean,measured_prr_tail");
    CsvWriter tab(TestsRoot() + "/results/G05_helper_reliability.csv", "runset,arm,cell,seeds,tbs,p_hd,p_needed_tail,p_rec_given_needed_tail,p_rec_given_needed_and_helper_decoded_tail,p_needed_worst,p_rec_given_needed_worst,coverage_gap_tail,csv_helper_drop_rate,true_drop_rate,csv_relay_needed,true_relay_needed");
    T.Artefact("results/G05_selector_calibration.csv");
    T.Artefact("results/G05_helper_reliability.csv");
    const std::vector<std::pair<std::string, std::string>> pairs = {{"R4_S1_H2", "R1"}, {"R5_S1_H2", "R2"}, {"R6_S1_H2", "R3"}};
    for (const std::string rs : {"CORE", "SYN_DIST", "SYN_MARGINAL"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { if (rs == "CORE") T.Skip("CORE data", "CORE manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        std::map<std::string, int> worstOf;
        for (const auto& rr : Select(all, "R1")) { Run r; if (r.Load(rr)) worstOf[rr.CellKey() + "|s" + std::to_string(rr.seed)] = WorstMemberByPrr(r); }
        struct Acc { long tbs{0}, hd{0}, needT{0}, recT{0}, recTdec{0}, needTdec{0}, needW{0}, recW{0}, gap{0}, needAnyOther{0}; double csvDrop{0}, trueDrop{0}, csvNeeded{0}, trueNeeded{0}; int runs{0}; };
        std::map<std::string, Acc> accByArmCell;
        std::map<std::string, std::map<std::string, std::pair<double, double>>> prrWorst; // cellseed -> arm -> (tail prr, worst prr)
        for (const auto& rr : all)
        {
            Run r;
            if (!r.Load(rr)) continue;
            const std::string cs = rr.CellKey() + "|s" + std::to_string(rr.seed);
            const int worst = worstOf.count(cs) ? worstOf[cs] : kMembers;
            prrWorst[cs][rr.arm] = {r.MemberCol("prr_pdb", kMembers), r.MemberCol("prr_pdb", worst)};
            if (!rr.IsHelperArm() || !r.hasAtt) continue;
            T.AddNRuns(1);
            Acc& A = accByArmCell[rr.arm + "|" + rr.CellKey()];
            ++A.runs;
            A.csvDrop += r.R("helper_drop_rate");
            A.csvNeeded += r.R("relay_needed");
            double trueNeeded = 0, tbs = 0, notDec = 0;
            for (const TbView& tv : WindowTbViews(r))
            {
                ++A.tbs; ++tbs;
                const int h = tv.helperNode;
                const bool dec = h >= 1 && h <= kMembers && tv.initial->status[h - 1] == 1;
                A.hd += dec;
                notDec += dec ? 0 : 1;
                bool anyOtherNeeds = false;
                for (int m = 1; m <= kMembers; ++m) if (m != h && tv.initial->status[m - 1] != 1) anyOtherNeeds = true;
                A.needAnyOther += anyOtherNeeds;
                trueNeeded += (anyOtherNeeds && dec) ? 1 : 0;
                // tail and empirical worst: needed = failed the initial; recovered = a later transmission delivered
                for (int which = 0; which < 2; ++which)
                {
                    const int m = which == 0 ? kMembers : worst;
                    if (m == h) continue;
                    const bool needed = tv.initial->status[m - 1] != 1;
                    if (!needed) continue;
                    bool rec = false;
                    for (size_t k = 1; k < tv.all.size(); ++k) rec = rec || (!r.IsSelf(*tv.all[k], m) && tv.all[k]->status[m - 1] == 1 && !r.DecodedBy(*tv.tb, static_cast<int>(k) - 1, m));
                    if (which == 0)
                    {
                        ++A.needT; A.recT += rec; A.needTdec += dec; A.recTdec += (dec && rec);
                        // hindsight best: another member that decoded the initial transmission (nearest to m by index) while the chosen helper did not
                        if (!dec)
                        {
                            for (int q = 1; q <= kMembers; ++q) if (q != m && q != h && tv.initial->status[q - 1] == 1) { ++A.gap; break; }
                        }
                    }
                    else { ++A.needW; A.recW += rec; }
                }
            }
            A.trueNeeded += trueNeeded;
            A.trueDrop += tbs > 0 ? notDec / tbs : 0;
            // selector calibration: mean predicted score of the applied helper vs measured PRR
            if (r.hasHsel && r.hselT.nrows > 0)
            {
                double s = 0; long n = 0;
                const auto& ap = r.hselT.D("applied");
                for (size_t i = 0; i < r.hselT.nrows; ++i)
                {
                    const int a = static_cast<int>(ap[i]);
                    if (a >= 1 && a <= kMembers) { s += r.hselT.D("score_m" + std::to_string(a))[i]; ++n; }
                }
                calib.Row({rs, rr.arm, rr.scenario, rr.ivd, rr.bg, rr.traffic, std::to_string(rr.seed), Fmt(n ? s / n : NAN, 6), Fmt(r.R("prr_mean"), 6), Fmt(r.MemberCol("prr_pdb", kMembers), 6)});
            }
        }
        for (const auto& [k, A] : accByArmCell)
        {
            const std::string arm = k.substr(0, k.find('|')), cell = k.substr(k.find('|') + 1);
            const Prop phd = Wilson(A.hd, A.tbs);
            const double pn = A.tbs ? static_cast<double>(A.needT) / A.tbs : NAN;
            tab.Row({rs, arm, cell, std::to_string(A.runs), FmtI(A.tbs), Fmt(phd.p, 5), Fmt(pn, 5), Fmt(A.needT ? static_cast<double>(A.recT) / A.needT : NAN, 5),
                     Fmt(A.needTdec ? static_cast<double>(A.recTdec) / A.needTdec : NAN, 5), Fmt(A.tbs ? static_cast<double>(A.needW) / A.tbs : NAN, 5), Fmt(A.needW ? static_cast<double>(A.recW) / A.needW : NAN, 5),
                     Fmt(A.needT ? static_cast<double>(A.gap) / A.needT : NAN, 5), Fmt(A.csvDrop / A.runs, 5), Fmt(A.trueDrop / A.runs, 5), Fmt(A.csvNeeded / A.runs, 4), Fmt(A.trueNeeded / A.runs, 4)});
            if (A.tbs >= 100 && A.needT >= 30)
            {
                const Prop pr = Wilson(A.recT, A.needT);
                T.Info(rs + " " + arm + " " + cell + ": P_hd / P(recovered | tail needed) / P(recovered | needed, helper decoded) / coverage gap",
                       Opt().Obs("P_hd " + Fmt(phd.p, 4) + " [" + Fmt(phd.lo, 4) + "," + Fmt(phd.hi, 4) + "]; P(rec|needed) " + Fmt(pr.p, 4) + " [" + Fmt(pr.lo, 4) + "," + Fmt(pr.hi, 4) + "] (n=" + FmtI(A.needT) + "); P(rec|needed, helper decoded) " +
                                 Fmt(A.needTdec ? static_cast<double>(A.recTdec) / A.needTdec : NAN, 4) + "; needed TBs where another member could have relayed but the chosen helper had not decoded: " + Fmt(A.needT ? 100.0 * A.gap / A.needT : NAN, 3) + " %"));
            }
            // F28
            const double d = A.csvDrop / A.runs, t = A.trueDrop / A.runs, n1 = A.csvNeeded / A.runs, n2 = A.trueNeeded / A.runs;
            if (std::fabs(d - t) > 0.02 || std::fabs(n1 - n2) > 0.05 * std::max(1.0, n2))
            {
                T.Xfail(rs + " " + arm + " " + cell + ": F28 helper_drop_rate / relay_needed of _run.csv differ from the correct definitions", Opt().Fnd("F28").Sev("M")
                            .Exp("helper_drop_rate = share of TBs whose helper did not decode the initial transmission; relay_needed = TBs where the helper decoded and a member still needed it")
                            .Obs("helper_drop_rate csv " + Fmt(d, 4) + " vs " + Fmt(t, 4) + "; relay_needed csv " + FmtI(static_cast<long>(std::lround(n1))) + " vs " + FmtI(static_cast<long>(std::lround(n2))))
                            .Note("run.csv relay_needed = SCHEDULED + NO_NACK events (opportunities) and helper_drop_rate divides a selection count by a decode count (main.cc:3978-3993)"));
            }
        }
        // helper arm must not reduce PRR_worst
        for (const auto& [h, base] : pairs)
        {
            std::map<std::string, std::vector<std::pair<double, double>>> byGroup;
            for (const auto& [cs, byArm] : prrWorst)
                if (byArm.count(h) && byArm.count(base))
                    byGroup[cs.substr(0, cs.rfind("|s"))].push_back({byArm.at(h).second - byArm.at(base).second, byArm.at(h).first - byArm.at(base).first});
            for (const auto& [group, v] : byGroup)
            {
                std::vector<double> w, t;
                for (const auto& p : v) { w.push_back(p.first); t.push_back(p.second); }
                const std::string pre = rs + " " + group + ": " + h + " - " + base + ": ";
                if (w.size() < 3) { T.Info(pre + "PRR_worst / PRR_tail difference", Opt().Obs(FmtI(static_cast<long>(w.size())) + " seeds: " + Fmt(Mean(w), 4) + " / " + Fmt(Mean(t), 4) + " (no verdict, n < 3)")); continue; }
                const MeanCI mw = MeanCi(w), mt = MeanCi(t);
                const bool noBg = group.find("/none/") != std::string::npos;
                if (noBg && mw.hi < 0)
                    T.Fail(pre + "the helper must not reduce PRR_worst (no background)", Opt().Exp("dPRR_worst CI not below 0").Obs(FmtCi(mw.mean, mw.lo, mw.hi, 4)).Sev("H"));
                else
                    T.Info(pre + "PRR_worst and PRR_tail difference (helper arm - leader-only counterpart)", Opt().Obs("worst " + FmtCi(mw.mean, mw.lo, mw.hi, 4) + ", tail " + FmtCi(mt.mean, mt.lo, mt.hi, 4)).Note(noBg ? "no-background cell: the helper does not reduce PRR_worst" : "with background: INFO"));
            }
        }
    }
    T.Info("selector calibration (H2)", Opt().Fnd("F24").Obs("predicted GPRR of the applied helper vs measured mean PRR per run: results/G05_selector_calibration.csv")
               .Note("F24: 'GPRR' is the selector's predicted group PRR in one place and the measured mean PRR in another; the predicted value is a per-link success probability model, the measured one includes collisions"));
    const std::string art = V.Write("G05_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
