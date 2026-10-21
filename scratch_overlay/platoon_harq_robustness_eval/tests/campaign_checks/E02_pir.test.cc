// E02_pir (C: CORE; W: SYN_DIST, SYN_NEAR): packet inter-reception time per member from the raw packet log.
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <algorithm>
#include <cmath>
#include <map>

using namespace ptest;

int
main()
{
    TestReport T("E02", "pir", "E");
    T.Summary("PIR per member = difference of consecutive reception times; identities (mean PIR = span/(n-1), mean PIR >= 0.95 T), "
              "E[PIR] ~ T/PRR in loss-free background, and the effect of the helper arms on the worst member's PIR p95.");
    T.AddRef("TS 22.186 / 5GAA PIR definition (inter-packet reception interval)");
    ViolationLog V;
    CsvWriter cdf(TestsRoot() + "/results/E02_pir_cdf.csv", "runset,scenario,ivd,bg,traffic,arm,member,quantile,pir_ms");
    T.Artefact("results/E02_pir_cdf.csv");

    // worst-member p95 per (cell, seed): arm -> value, for the paired helper comparison
    std::map<std::string, std::map<std::string, double>> p95Worst; // cellseed -> arm -> p95 of worst member (lowest PRR in R1.. same arm)
    long idBad = 0, idCells = 0, floorBad = 0, floorCells = 0, e1Cells = 0, e1Fail = 0, e1CellsBg = 0, e1FailBg = 0;
    std::map<std::string, std::string> firstIvd;

    const std::vector<std::string> sets = {"CORE", "SYN_DIST", "SYN_NEAR"};
    for (const std::string& rs : sets)
    {
        const auto all = LoadManifest(rs);
        if (all.empty())
        {
            if (rs == "CORE")
            {
                T.Skip("CORE data", "CORE manifest not present (campaign stage not run yet)");
            }
            continue;
        }
        T.AddRunset(rs);
        for (const auto& rr : all)
        {
            Run r;
            if (!r.Load(rr))
            {
                continue;
            }
            T.AddNRuns(1);
            const double Tms = (rr.traffic.rfind("periodic1", 0) == 0) ? 100.0 : (rr.traffic.rfind("periodic2", 0) == 0) ? 10.0 : 0.0;
            double worstPrr = 2, worstP95 = NAN;
            for (int m = 1; m <= kMembers; ++m)
            {
                const auto& rows = r.pkts[m - 1];
                if (rows.size() < 20)
                {
                    continue;
                }
                std::vector<double> trx;
                double gen = 0;
                for (size_t i = 0; i < rows.size(); ++i)
                {
                    if (rows[i].rx)
                    {
                        trx.push_back(rows[i].tTx + rows[i].delayMs / 1e3);
                    }
                    if (i > 0)
                    {
                        gen += rows[i].tTx - rows[i - 1].tTx;
                    }
                }
                if (trx.size() < 10)
                {
                    continue;
                }
                std::sort(trx.begin(), trx.end());
                std::vector<double> pir;
                for (size_t i = 1; i < trx.size(); ++i)
                {
                    pir.push_back((trx[i] - trx[i - 1]) * 1e3);
                }
                const double mean = Mean(pir);
                const double span = (trx.back() - trx.front()) * 1e3 / static_cast<double>(trx.size() - 1);
                ++idCells;
                if (std::fabs(mean - span) > 1e-6 * std::max(1.0, span))
                {
                    ++idBad;
                    V.Add("mean PIR = span/(n-1)", rs + "/" + rr.arm + "/" + rr.tag + " member " + std::to_string(m));
                }
                const double meanGen = rows.size() > 1 ? gen * 1e3 / static_cast<double>(rows.size() - 1) : NAN;
                const double Tuse = Tms > 0 ? Tms : meanGen;
                ++floorCells;
                if (mean < 0.95 * Tuse)
                {
                    ++floorBad;
                    V.Add("mean PIR >= 0.95 T", rs + "/" + rr.arm + "/" + rr.tag + " member " + std::to_string(m) + " mean " + Fmt(mean, 5) + " T " + Fmt(Tuse, 5));
                }
                long rx = 0;
                for (const auto& p : rows) rx += p.rx;
                const double prr = static_cast<double>(rx) / rows.size();
                // E[PIR] ~ T / PRR (periodic classes, i.i.d. losses); bursty with background
                if (Tms > 0 && prr > 0.3)
                {
                    const double want = Tms / prr;
                    const bool ok = std::fabs(mean - want) / want <= 0.15;
                    if (rr.bg == "none")
                    {
                        ++e1Cells;
                        e1Fail += ok ? 0 : 1;
                        if (!ok) V.Add("E[PIR] ~ T/PRR within 15% (no background)", rs + "/" + rr.arm + "/" + rr.tag + " member " + std::to_string(m) + " mean " + Fmt(mean, 5) + " T/PRR " + Fmt(want, 5));
                    }
                    else
                    {
                        ++e1CellsBg;
                        e1FailBg += ok ? 0 : 1;
                    }
                }
                if (prr < worstPrr)
                {
                    worstPrr = prr;
                    worstP95 = Quantile(pir, 0.95);
                }
                // CDF artefact: one cell per scenario (first seed of the first ivd / bg / traffic seen)
                const std::string cellKey = rs + "/" + rr.scenario + "/" + rr.ivd + "/" + rr.bg + "/" + rr.traffic + "/" + std::to_string(rr.seed) + "/" + rr.arm;
                const std::string sceneKey = rs + "/" + rr.scenario + "/" + rr.bg + "/" + rr.traffic;
                if (rr.seed == 1 && (m == 1 || m == 4 || m == 8))
                {
                    auto it = firstIvd.find(sceneKey);
                    if (it == firstIvd.end())
                    {
                        firstIvd[sceneKey] = rr.ivd;
                    }
                    if (firstIvd[sceneKey] == rr.ivd)
                    {
                        for (int q = 0; q <= 100; q += 2)
                        {
                            cdf.Row({rs, rr.scenario, rr.ivd, rr.bg, rr.traffic, rr.arm, std::to_string(m), std::to_string(q / 100.0), Fmt(Quantile(pir, q / 100.0), 6)});
                        }
                    }
                }
                (void)cellKey;
            }
            if (!std::isnan(worstP95))
            {
                p95Worst[rs + "/" + rr.CellKey() + "/s" + std::to_string(rr.seed)][rr.arm] = worstP95;
            }
        }
    }
    if (idCells == 0)
    {
        return T.Finish();
    }
    T.Check("mean PIR == (t_last - t_first)/(n-1) for every member of every run", idBad == 0, Opt().Exp("0 of " + FmtI(idCells)).Obs(FmtI(idBad)).Tol("1e-6 relative").Sev("M"));
    T.Check("mean PIR >= 0.95 T (T = class period, measured mean generation interval for aperiodic1tb)", floorBad == 0, Opt().Exp("0 of " + FmtI(floorCells)).Obs(FmtI(floorBad)).Tol("0").Sev("M"));
    if (e1Cells > 0)
    {
        T.Check("periodic classes without background: |mean PIR - T/PRR| / (T/PRR) <= 0.15", e1Fail == 0,
                Opt().Exp("0 of " + FmtI(e1Cells) + " member cells").Obs(FmtI(e1Fail)).Tol("0.15 relative").Sev("L")
                    .Note("i.i.d. loss model; the approximation degrades with correlated losses of far members"));
    }
    if (e1CellsBg > 0)
    {
        T.Info("periodic classes with background: members outside the 15% band of T/PRR", Opt().Obs(FmtI(e1FailBg) + " of " + FmtI(e1CellsBg) + " member cells").Note("bursty collisions; INFO per the plan"));
    }
    // directional: helper arms lower p95 of the worst member than the leader-only counterpart (paired)
    {
        const std::vector<std::pair<std::string, std::string>> pairs = {{"R4_S1_H2", "R1"}, {"R5_S1_H2", "R2"}, {"R6_S1_H2", "R3"}};
        for (const auto& [h, base] : pairs)
        {
            std::vector<double> a, b;
            for (const auto& [cell, byArm] : p95Worst)
            {
                if (byArm.count(h) && byArm.count(base))
                {
                    a.push_back(byArm.at(h));
                    b.push_back(byArm.at(base));
                }
            }
            if (a.size() < 3)
            {
                T.Info("PIR p95 of the worst member, " + h + " vs " + base, Opt().Obs(FmtI(static_cast<long>(a.size())) + " pairs: fewer than 3, no verdict (section 7.3)"));
                continue;
            }
            const DiffCI d = Paired(a, b);
            T.Info("PIR p95 of the worst member, " + h + " - " + base + " (paired over cells and seeds, n=" + FmtI(static_cast<long>(a.size())) + ")",
                   Opt().Obs(FmtCi(d.diff, d.lo, d.hi, 4) + " ms" + (d.hi < 0 ? " -> helper lowers the p95" : d.lo > 0 ? " -> helper raises the p95" : " -> no clear difference")));
        }
    }
    const std::string art = V.Write("E02_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
