// D02_selection_invariants (C: CORE hselOn arms; W: SYN_DIST): chosen in 1..8, applied == chosen, cam age >= decision delay, one _hsel
// row per ndi=1 TB; on the static SYN_DIST geometries the logged scores equal the independent Kuo recomputation; helper-choice
// distribution (F09) and the per-HARQ-id overwrite risk (F05).
#include "common/csv.h"
#include "common/harness.h"
#include "common/kuo_model.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <cmath>
#include <map>

using namespace ptest;

int
main()
{
    TestReport T("D02", "selection_invariants", "D");
    T.Summary("The per-TB helper choice is valid (member 1..8, applied == chosen, one row per TB) and, on static geometries, equals the "
              "independent Kuo argmax; the Kuo choice is nearly static on the highway (F09).");
    T.AddRef("README Kuo eq. 12-13; helper_select.cc (HelperSelector::Choose)");
    ViolationLog V;
    std::map<std::string, std::map<int, long>> dist; // cell -> member -> TBs

    for (const std::string rs : {"CORE", "SYN_DIST", "SYN_MARGINAL"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty())
        {
            if (rs == "CORE")
            {
                T.Skip("CORE quick data", "CORE manifest not present (campaign stage not run yet)");
            }
            continue;
        }
        T.AddRunset(rs);
        long runs = 0, rows = 0, badChosen = 0, notApplied = 0, ageBad = 0, tbs = 0, tbNoRow = 0, tbMultiRow = 0, tbSingle = 0;
        double maxScoreDiff = 0;
        long scoreCmp = 0, argmaxMismatch = 0, argmaxNear = 0;
        long f05 = 0, f05Tbs = 0;
        for (const auto& rr : all)
        {
            if (!rr.IsHelperArm() || rr.leaderSps == false)
            {
                // SPSDYN has its own run-set; here only SPS runs
            }
            if (!rr.IsHelperArm())
            {
                continue;
            }
            Run r;
            if (!r.Load(rr) || !r.hasHsel || !r.hasAtt)
            {
                continue;
            }
            ++runs;
            T.AddNRuns(1);
            const auto& chosen = r.hselT.D("chosen");
            const auto& applied = r.hselT.D("applied");
            const auto& age = r.hselT.D("cam_age_ms");
            const auto& tn = r.hselT.D("t_ndi");
            const auto& td = r.hselT.D("t_decide");
            const auto& hid = r.hselT.D("harq_id");
            const auto& nsub = r.hselT.D("n_subch");
            std::map<int64_t, int> rowsAtSlot;
            for (size_t i = 0; i < r.hselT.nrows; ++i)
            {
                ++rows;
                badChosen += (chosen[i] < 1 || chosen[i] > 8) ? 1 : 0;
                notApplied += (applied[i] != chosen[i]) ? 1 : 0;
                ageBad += (age[i] < r.R("helper_decision_ms", 0) - 1e-9) ? 1 : 0;
                rowsAtSlot[std::llround(tn[i] / kSlotS)]++;
            }
            const std::string cell = rs + "/" + rr.scenario + "/ivd" + rr.ivd + "/" + rr.bg + "/" + rr.arm;
            for (const TbView& tv : WindowTbViews(r))
            {
                ++tbs;
                auto it = rowsAtSlot.find(tv.initial->slot);
                const int n = it == rowsAtSlot.end() ? 0 : it->second;
                if (n == 0) { if (r.GrantSlotsAt(tv.initial->t) == 1) ++tbSingle; else ++tbNoRow; }
                tbMultiRow += (n > 1);
                dist[cell][tv.helperNode]++;
                // F05: a later selection on the same HARQ id before this TB's last copy
                double tlast = 0;
                for (const PTx* x : tv.all) tlast = std::max(tlast, x->t);
                bool over = false;
                for (size_t i = 0; i < r.hselT.nrows && !over; ++i)
                {
                    over = static_cast<int>(hid[i]) == tv.initial->harq && tn[i] > tv.initial->t + 1e-9 && td[i] > tv.initial->t && td[i] <= tlast;
                }
                ++f05Tbs;
                f05 += over ? 1 : 0;
            }
            // SYN_DIST: independent recomputation of the logged scores (static positions)
            if (rs == "SYN_DIST")
            {
                const double sp = std::stod(rr.ivd);
                std::vector<double> dL(9, 0.0);
                std::vector<std::vector<double>> dM(9, std::vector<double>(9, 0.0));
                for (int a = 0; a <= 8; ++a)
                {
                    dL[a] = sp * a;
                    for (int b = 0; b <= 8; ++b) dM[a][b] = sp * std::abs(a - b);
                }
                for (size_t i = 0; i < r.hselT.nrows; i += std::max<size_t>(1, r.hselT.nrows / 40))
                {
                    const kuo::Scores s = kuo::KuoScores(dL, dM, true, static_cast<uint32_t>(nsub[i]), false);
                    double md = 0;
                    for (int m = 1; m <= 8; ++m)
                    {
                        md = std::max(md, std::fabs(r.hselT.D("score_m" + std::to_string(m))[i] - s.g[m - 1]));
                    }
                    maxScoreDiff = std::max(maxScoreDiff, md);
                    ++scoreCmp;
                    if (static_cast<uint32_t>(chosen[i]) != s.chosen)
                    {
                        const double a = s.g[static_cast<int>(chosen[i]) - 1], b = s.g[s.chosen - 1];
                        if (std::fabs(a - b) < 1e-3) ++argmaxNear; else { ++argmaxMismatch; V.Add("SYN_DIST chosen != independent argmax", rr.tag + " row " + std::to_string(i)); }
                    }
                }
            }
        }
        if (runs == 0)
        {
            continue;
        }
        const std::string pre = rs + ": ";
        T.Check(pre + "chosen member in 1..8 for every _hsel row (the leader is never chosen)", badChosen == 0, Opt().Exp("0 of " + FmtI(rows)).Obs(FmtI(badChosen)).Tol("0").Sev("H"));
        T.Check(pre + "applied == chosen (no shadow mode)", notApplied == 0, Opt().Exp("0").Obs(FmtI(notApplied)).Tol("0").Sev("H"));
        T.Check(pre + "cam_age_ms >= helperDecisionMs", ageBad == 0, Opt().Exp("0").Obs(FmtI(ageBad)).Tol("0").Sev("M"));
        T.Check(pre + "every ndi=1 leader TB has exactly one _hsel row at its transmission time", tbNoRow == 0 && tbMultiRow == 0,
                Opt().Exp(FmtI(tbs) + " TBs with one row").Obs(FmtI(tbNoRow) + " without a row, " + FmtI(tbMultiRow) + " with several; " + FmtI(tbSingle) + " TBs sent under a single-slot SPS grant have no row by design (no handed slot, the selector is not run)").Tol("0").Sev("M").Note("see C01 / F33 for the single-slot grants"));
        if (rs == "SYN_DIST" && scoreCmp > 0)
        {
            T.Check(pre + "logged scores == independent Kuo recomputation (static positions, " + FmtI(scoreCmp) + " rows sampled)", maxScoreDiff < 1e-3,
                    Opt().Exp("max |diff| < 1e-3").Obs(Fmt(maxScoreDiff, 3)).Tol("1e-3").Sev("H"));
            T.Check(pre + "chosen helper == independent argmax (near-ties within 1e-3 allowed)", argmaxMismatch == 0,
                    Opt().Exp("0 mismatches").Obs(FmtI(argmaxMismatch) + " mismatches, " + FmtI(argmaxNear) + " near-ties").Tol("0").Sev("H"));
        }
        if (f05Tbs > 0)
        {
            T.CheckXfail(pre + "F05: the per-HARQ-id choice is not overwritten before the TB's last copy", f05 == 0,
                         Opt().Fnd("F05").Sev("M").Exp("0").Obs(FmtI(f05) + " of " + FmtI(f05Tbs) + " TBs")
                             .Note("chosenOfHarq is keyed by HARQ id (main.cc:330, 566-576)"));
        }
    }
    // F09: distribution
    {
        std::string d;
        double maxShare = 0;
        for (const auto& [cell, h] : dist)
        {
            long tot = 0, best = 0;
            int bm = 0;
            for (const auto& [m, n] : h) { tot += n; if (n > best) { best = n; bm = m; } }
            maxShare = std::max(maxShare, tot ? static_cast<double>(best) / tot : 0.0);
            if (d.size() < 1500)
            {
                d += cell + ": member " + std::to_string(bm) + " " + Fmt(tot ? 100.0 * best / tot : 0.0, 3) + "% of " + FmtI(tot) + "; ";
            }
        }
        T.Info("F09: helper-choice concentration per cell (Kuo ignores vehicle blockage)", Opt().Fnd("F09").Obs(d.empty() ? "no data" : d)
                   .Note("the modal member's share of the TBs per (run-set, scenario, ivd, bg, arm) cell; a share near 100% means the choice is static"));
        T.Metric("max_modal_helper_share", maxShare);
    }
    const std::string art = V.Write("D02_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
