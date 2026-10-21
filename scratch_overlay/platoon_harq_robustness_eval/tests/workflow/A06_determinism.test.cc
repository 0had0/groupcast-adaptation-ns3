// A06_determinism (W: DET): same config twice -> identical outputs; diagnostics (--writeAtt/--writeAllTx/--writeGrants)
// do not change the KPI outputs (F04); --collCond is documented as RNG-affecting (INFO).
#include "common/csv.h"
#include "common/harness.h"
#include "common/runsets.h"

#include <map>

using namespace ptest;

int
main()
{
    TestReport T("A06", "determinism", "A");
    T.Summary("Runs are deterministic (identical outputs for identical config and seed); the diagnostic output flags do "
              "not perturb the KPI outputs (F04).");
    T.AddRef("TEST_PLAN F04");
    T.AddRunset("DET");

    const auto runs = LoadManifest("DET");
    T.SetNRuns(static_cast<long>(runs.size()));
    if (runs.empty())
    {
        T.Skip("DET", "no DET manifest: run campaign/run_matrix.py --runset DET first");
        return T.Finish();
    }
    // wall-clock dependent columns of _run.csv
    const std::vector<std::string> wall = {"wall_s", "sel_cpu_us_mean"};
    const std::vector<std::string> files = {"_run.csv",   "_member.csv", "_pkt.csv",  "_ltx.csv", "_htx.csv",
                                            "_nack.csv", "_coll.csv"};
    const std::vector<std::string> diagFiles = {"_att.csv", "_alltx.csv", "_hsel.csv", "_grants.csv"};

    std::map<std::string, std::map<std::string, RunRef>> by; // arm -> variant -> run
    for (const auto& r : runs)
    {
        const auto p = r.arm.find("__");
        if (p == std::string::npos)
        {
            continue;
        }
        by[r.arm.substr(0, p)][r.arm.substr(p + 2)] = r;
    }
    long totalDiffF04 = 0;
    for (const auto& [arm, vars] : by)
    {
        auto get = [&](const std::string& v) -> const RunRef* {
            auto it = vars.find(v);
            return it == vars.end() ? nullptr : &it->second;
        };
        const RunRef *a = get("on_a"), *b = get("on_b"), *off = get("off"), *co = get("coll");

        // 1. determinism: on_a vs on_b, every file
        if (a && b)
        {
            long nd = 0, nFiles = 0;
            std::string first;
            for (const auto& suf : files)
            {
                bool miss = false;
                std::string fd;
                const long d = CompareTables(a->prefix + suf, b->prefix + suf, suf == "_run.csv" ? wall : std::vector<std::string>{}, &fd, &miss);
                if (!miss)
                {
                    ++nFiles;
                    nd += d;
                    if (d && first.empty())
                    {
                        first = suf + ": " + fd;
                    }
                }
            }
            for (const auto& suf : diagFiles)
            {
                bool miss = false;
                std::string fd;
                const long d = CompareTables(a->prefix + suf, b->prefix + suf, {}, &fd, &miss);
                if (!miss)
                {
                    ++nFiles;
                    nd += d;
                    if (d && first.empty())
                    {
                        first = suf + ": " + fd;
                    }
                }
            }
            T.Check(arm + "/identical config twice -> identical outputs (" + std::to_string(nFiles) + " files)",
                    nd == 0 && nFiles > 0,
                    Opt().Exp("0 differing cells (wall_s, sel_cpu_us_mean excluded)").Obs(FmtI(nd) + " differing cells" +
                                                                                           (first.empty() ? "" : "; first " + first))
                        .Tol("0").Sev("H"));
        }
        // 2. diagnostics neutrality: on_a vs off, all files that exist in both
        if (a && off)
        {
            long nd = 0, nFiles = 0;
            std::string first;
            for (const auto& suf : files)
            {
                bool miss = false;
                std::string fd;
                const long d = CompareTables(a->prefix + suf, off->prefix + suf, suf == "_run.csv" ? wall : std::vector<std::string>{}, &fd, &miss);
                if (!miss)
                {
                    ++nFiles;
                    nd += d;
                    if (d && first.empty())
                    {
                        first = suf + ": " + fd;
                    }
                }
            }
            totalDiffF04 += nd;
            // the diag files must not exist in the off run
            bool diagAbsent = true;
            for (const auto& suf : {std::string("_att.csv"), std::string("_alltx.csv"), std::string("_grants.csv")})
            {
                if (!ResolveCsv(off->prefix + suf).empty())
                {
                    diagAbsent = false;
                }
            }
            T.Check(arm + "/diagnostics off vs on: KPI outputs identical (" + std::to_string(nFiles) + " files)",
                    nd == 0 && nFiles > 0,
                    Opt().Exp("_run.csv (all columns but wall-clock), _member/_pkt/_ltx/_htx/_nack identical")
                        .Obs(FmtI(nd) + " differing cells" + (first.empty() ? "" : "; first " + first))
                        .Tol("0").Sev("H").Fnd("F04")
                        .Note("diag flags: --writeAtt --writeAllTx --writeGrants; if this fails, the campaign must run diag-off "
                              "duplicates for the KPI comparisons"));
            T.Info(arm + "/diag-off run writes no diagnostic CSVs", Opt().Obs(diagAbsent ? "_att/_alltx/_grants not written" : "diagnostic CSVs were written").Note("_hsel.csv is written by the selector arms regardless of the diag flags"));
        }
        // 3. collCond
        if (a && co)
        {
            long nd = 0, nFiles = 0, ndKpi = 0, nKpiFiles = 0;
            std::string first, firstKpi, perFile;
            for (const auto& suf : files)
            {
                bool miss = false;
                std::string fd;
                std::vector<std::string> ex = wall;
                ex.push_back("coll_cond"); // the column records the flag itself
                const long d = CompareTables(a->prefix + suf, co->prefix + suf, suf == "_run.csv" ? ex : std::vector<std::string>{}, &fd, &miss);
                if (!miss)
                {
                    ++nFiles;
                    nd += d;
                    perFile += suf + " " + FmtI(d) + "; ";
                    const bool kpi = (suf == "_member.csv" || suf == "_pkt.csv" || suf == "_ltx.csv" || suf == "_htx.csv" || suf == "_nack.csv");
                    if (kpi)
                    {
                        ++nKpiFiles;
                        ndKpi += d;
                        if (d && firstKpi.empty())
                        {
                            firstKpi = suf + ": " + fd;
                        }
                    }
                    if (d && first.empty())
                    {
                        first = suf + ": " + fd;
                    }
                }
            }
            T.CheckXfail(arm + "/--collCond=true: the KPI outputs (_member, _pkt, _ltx, _htx, _nack) are identical to the default run (no extra RNG draws)", ndKpi == 0,
                         Opt().Fnd("F04").Sev("M").Exp("0 differing cells").Obs(FmtI(ndKpi) + " differing cells over " + FmtI(nKpiFiles) + " KPI files" + (firstKpi.empty() ? "" : "; first " + firstKpi))
                             .Tol("0").Note("F04 expected --collCond to change random draws; the only differences are the diagnostic condition columns of _coll.csv and counters of _run.csv (per file: " + perFile + ")"));
            T.Info(arm + "/--collCond=true vs default: " + (nd == 0 ? "identical" : "DIFFERS"),
                   Opt().Obs(FmtI(nd) + " differing cells over " + FmtI(nFiles) + " files" + (first.empty() ? "" : "; first " + first))
                       .Note("--collCond issues extra channel-condition queries (documented as RNG-affecting); the campaign "
                             "does not use it"));
        }
    }
    T.Metric("f04_total_diff_cells", static_cast<double>(totalDiffF04));
    T.MetricS("campaign_needs_diag_off_duplicates", totalDiffF04 > 0 ? "yes" : "no");
    return T.Finish();
}
