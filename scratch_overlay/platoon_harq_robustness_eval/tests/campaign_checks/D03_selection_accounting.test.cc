// D03_selection_accounting (C: CORE): the selector runs once per published grant, including SPS periods without data (F06): the
// run.csv helper_assigned / helper_changes_per_s / helper_share_m* are inflated; recompute them per TB.
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
    TestReport T("D03", "selection_accounting", "D");
    T.Summary("The helper-selection statistics of _run.csv count selections, not TBs: rows per ndi=1 TB and the corrected changes/s and "
              "shares are reported (F06).");
    T.AddRef("TEST_PLAN F06 (main.cc:3978-3993)");
    ViolationLog V;
    std::map<std::string, std::vector<double>> ratio;   // "traffic/sps" -> rows per TB per run
    std::map<std::string, std::vector<double>> chgCsv, chgTb;
    long cmpRuns = 0;

    for (const std::string rs : {"CORE", "SPSDYN"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty())
        {
            T.Skip(rs + " data", rs + " manifest not present (campaign stage not run yet)");
            continue;
        }
        T.AddRunset(rs);
        for (const auto& rr : all)
        {
            if (!rr.IsHelperArm())
            {
                continue;
            }
            Run r;
            if (!r.Load(rr) || !r.hasHsel)
            {
                continue;
            }
            T.AddNRuns(1);
            // rows in the KPI window (by t_ndi) and TBs
            const auto& tn = r.hselT.D("t_ndi");
            const auto& ap = r.hselT.D("applied");
            long rowsWin = 0;
            std::vector<int> appliedSeq;
            for (size_t i = 0; i < r.hselT.nrows; ++i)
            {
                if (tn[i] >= r.kpiStart && tn[i] < r.kpiEnd)
                {
                    ++rowsWin;
                    appliedSeq.push_back(static_cast<int>(ap[i]));
                }
            }
            std::vector<TbView> tbs = WindowTbViews(r);
            const long nTb = static_cast<long>(tbs.size());
            if (nTb == 0)
            {
                continue;
            }
            ratio[rs + "/" + rr.traffic + (rr.leaderSps ? "/sps" : "/dyn")].push_back(static_cast<double>(rowsWin) / nTb);
            // corrected: only rows that start a TB, in TB order
            std::vector<int> perTb;
            std::sort(tbs.begin(), tbs.end(), [](const TbView& a, const TbView& b) { return a.initial->t < b.initial->t; });
            for (const TbView& tv : tbs) perTb.push_back(tv.helperNode);
            long ch = 0;
            for (size_t k = 1; k < perTb.size(); ++k) ch += (perTb[k] != perTb[k - 1]) ? 1 : 0;
            long chAll = 0;
            for (size_t k = 1; k < appliedSeq.size(); ++k) chAll += (appliedSeq[k] != appliedSeq[k - 1]) ? 1 : 0;
            const double win = r.kpiEnd - r.kpiStart;
            chgTb[rs + "/" + rr.traffic].push_back(ch / win);
            chgCsv[rs + "/" + rr.traffic].push_back(r.R("helper_changes_per_s"));
            // the run.csv value must equal the all-rows recomputation (exactness of my reading of the metric)
            // run.csv prints the rate with 5 significant digits, so compare with a relative tolerance of 2e-5 (not 1e-6)
            if (std::fabs(chAll / win - r.R("helper_changes_per_s")) > 2e-5 * std::max(1.0, std::fabs(chAll / win)))
            {
                V.Add("helper_changes_per_s equals the all-rows recount", rs + "/" + rr.arm + "/" + rr.tag + " recount " + Fmt(chAll / win, 6) + " csv " + Fmt(r.R("helper_changes_per_s"), 6));
            }
            ++cmpRuns;
        }
    }
    if (ratio.empty())
    {
        return T.Finish();
    }
    for (const auto& [k, v] : ratio)
    {
        const MeanCI m = MeanCi(v);
        const bool inflated = m.mean > 1.0 + 0.02;
        const std::string name = "selection rows per ndi=1 TB, " + k;
        if (inflated)
        {
            T.Xfail(name, Opt().Fnd("F06").Sev("M").Exp("1.0 (one selection per TB)").Obs(m.ok() ? FmtCi(m.mean, m.lo, m.hi, 4) : Fmt(m.mean, 4))
                              .Note("the selector runs at every published grant, including SPS periods with no data (aperiodic1tb reserves every 20 ms but "
                                    "sends a packet every ~100 ms): helper_assigned, helper_changes_per_s and helper_share_m* are inflated by this factor"));
        }
        else
        {
            T.Pass(name, Opt().Fnd("F06").Exp("1.0").Obs(m.ok() ? FmtCi(m.mean, m.lo, m.hi, 4) : Fmt(m.mean, 4)).Tol("+-0.02"));
        }
        T.Metric("rows_per_tb_" + k, m.mean);
    }
    for (const auto& [k, v] : chgCsv)
    {
        const MeanCI a = MeanCi(v), b = MeanCi(chgTb[k]);
        T.Info("helper_changes_per_s " + k + ": run.csv vs per-TB recount", Opt().Fnd("F06").Obs("run.csv " + Fmt(a.mean, 4) + " /s, per TB " + Fmt(b.mean, 4) + " /s"));
    }
    T.Check("helper_changes_per_s of run.csv equals the all-selection-rows recount (metric semantics understood)", V.Count("helper_changes_per_s equals the all-rows recount") == 0,
            Opt().Exp("0 of " + FmtI(cmpRuns)).Obs(FmtI(V.Count("helper_changes_per_s equals the all-rows recount"))).Tol("relative 2e-5 (run.csv prints 5 significant digits)").Sev("L"));
    const std::string art = V.Write("D03_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
