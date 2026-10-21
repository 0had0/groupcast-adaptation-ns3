// G06_distance_link_conditions (W: SYN_DIST; C: CORE): PRR_pdb versus the leader distance of the member, per arm; ordering of the arms; IVD effect on
// the tail; roundabout versus highway.
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
    TestReport T("G06", "distance_link_conditions", "G");
    T.Summary("PRR_pdb decreases with the leader distance for every arm; with no background the helper arms are not worse than the leader-only arms "
              "beyond the helper; the tail PRR does not improve with a larger IVD; roundabout versus highway.");
    T.AddRef("TR 37.885 V2V path loss / blockage; TEST_PLAN G06");
    CsvWriter curves(TestsRoot() + "/results/G06_prr_vs_distance.csv", "runset,scenario,ivd,bg,traffic,arm,member,mean_dist_m,prr_pdb,n_runs");
    T.Artefact("results/G06_prr_vs_distance.csv");
    for (const std::string rs : {"SYN_DIST", "CORE"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { if (rs == "CORE") T.Skip("CORE data", "CORE manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        struct P { std::vector<double> prr, dist; };
        std::map<std::string, std::map<std::string, std::vector<P>>> data;  // cell -> arm -> member (as index) accumulate
        std::map<std::string, std::map<std::string, std::vector<std::vector<double>>>> prrBy, distBy; // cell -> arm -> member -> values
        for (const auto& rr : all)
        {
            Run r;
            if (!r.Load(rr)) continue;
            T.AddNRuns(1);
            const std::string cell = rr.scenario + "/" + (rs == "SYN_DIST" ? "sp" : "ivd") + rr.ivd + "/" + rr.bg + "/" + rr.traffic;
            auto& pv = prrBy[cell][rr.arm];
            auto& dv = distBy[cell][rr.arm];
            if (pv.empty()) { pv.assign(kMembers, {}); dv.assign(kMembers, {}); }
            for (int m = 1; m <= kMembers; ++m) { pv[m - 1].push_back(r.MemberCol("prr_pdb", m)); dv[m - 1].push_back(r.MemberCol("mean_dist_m", m)); }
        }
        long cellsMono = 0, cellsN = 0;
        std::string nonMono;
        for (const auto& [cell, byArm] : prrBy)
            for (const auto& [arm, pv] : byArm)
            {
                std::vector<double> x, y;
                for (int m = 1; m <= kMembers; ++m)
                {
                    x.push_back(Mean(distBy[cell][arm][m - 1]));
                    y.push_back(Mean(pv[m - 1]));
                    curves.Row({rs, cell.substr(0, cell.find('/')), cell, "", "", arm, std::to_string(m), Fmt(x.back(), 6), Fmt(y.back(), 6), std::to_string(pv[m - 1].size())});
                }
                const Corr c = Spearman(x, y);
                ++cellsN;
                if (DirectionalOk(c, -1)) ++cellsMono;
                else if (nonMono.size() < 300) nonMono += cell + "/" + arm + " rho=" + Fmt(c.rho, 3) + "; ";
            }
        if (cellsN == 0) continue;
        if (cellsMono == cellsN) T.Pass(rs + ": PRR_pdb decreases with the leader distance in every (cell, arm) (Spearman, section 7.3)", Opt().Exp("rho < 0 with p < 0.05 or |rho| >= 0.6").Obs(FmtI(cellsMono) + " of " + FmtI(cellsN)));
        else T.Info(rs + ": PRR_pdb vs leader distance not directional in some (cell, arm)", Opt().Exp("negative rank correlation").Obs(FmtI(cellsMono) + " of " + FmtI(cellsN) + " directional; " + nonMono).Note("INFO per section 7.3 (saturated PRR = 1 members create ties)"));
        // ordering at equal distance, no background: helper arms vs leader-only arms for members beyond the helper
        for (const auto& [h, base] : std::vector<std::pair<std::string, std::string>>{{"R4_S1_H2", "R1"}, {"R5_S1_H2", "R2"}, {"R6_S1_H2", "R3"}})
        {
            std::vector<double> a, b;
            for (const auto& [cell, byArm] : prrBy)
            {
                if (cell.find("/none/") == std::string::npos || !byArm.count(h) || !byArm.count(base)) continue;
                for (int m = 5; m <= kMembers; ++m) { a.push_back(Mean(byArm.at(h)[m - 1])); b.push_back(Mean(byArm.at(base)[m - 1])); }
            }
            if (a.size() < 3) continue;
            const DiffCI d = Paired(a, b);
            T.Info(rs + ": members 5-8, no background: " + h + " - " + base + " (paired over cells and members)", Opt().Obs(FmtCi(d.diff, d.lo, d.hi, 4) + (d.lo > 0 ? " -> helper arm better" : d.hi < 0 ? " -> helper arm worse" : " -> no clear difference")));
        }
        // IVD effect on the tail (CORE only, if several ivds exist)
        if (rs == "CORE")
        {
            std::map<std::string, std::map<int, std::vector<double>>> tail; // scenario|bg|traffic|arm -> ivd -> tail prr
            for (const auto& [cell, byArm] : prrBy)
            {
                const auto p1 = cell.find('/'), p2 = cell.find('/', p1 + 1);
                const int ivd = std::stoi(cell.substr(p1 + 4, p2 - p1 - 4));
                const std::string rest = cell.substr(0, p1) + cell.substr(p2);
                for (const auto& [arm, pv] : byArm) tail[rest + "|" + arm][ivd].push_back(Mean(pv[kMembers - 1]));
            }
            long multi = 0, ok = 0;
            for (const auto& [k, byIvd] : tail)
            {
                if (byIvd.size() < 2) continue;
                ++multi;
                double prev = 2; bool mono = true;
                for (auto it = byIvd.begin(); it != byIvd.end(); ++it) { const double m = Mean(it->second); if (m > prev + 0.02) mono = false; prev = m; }
                // smaller ivd => higher PRR: ascending ivd must be non-increasing
                ok += mono;
            }
            if (multi == 0) T.Skip("IVD effect on the tail PRR", "the quick profile has a single IVD (30 m); the full profile has 5/15/30");
            else T.Info("IVD effect on the tail PRR: non-increasing with the IVD in " + FmtI(ok) + " of " + FmtI(multi) + " (cell, arm) groups", Opt().Obs(FmtI(ok) + " of " + FmtI(multi)));
        }
        // roundabout vs highway at ivd30 (CORE)
        if (rs == "CORE")
        {
            std::map<std::string, std::vector<double>> prr, nlos;
            for (const auto& rr : all)
            {
                if (rr.ivd != "30" || rr.bg != "none") continue;
                Run r;
                if (!r.Load(rr)) continue;
                prr[rr.scenario].push_back(r.R("prr_mean"));
                double s = 0; for (int m = 1; m <= kMembers; ++m) s += r.MemberCol("frac_nlos", m) / kMembers;
                nlos[rr.scenario].push_back(s);
            }
            if (prr.count("highway") && prr.count("roundabout"))
                T.Info("roundabout vs highway at ivd 30, no background", Opt().Obs("PRR_mean highway " + Fmt(Mean(prr["highway"]), 4) + " vs roundabout " + Fmt(Mean(prr["roundabout"]), 4) + "; building-NLOS share highway " + Fmt(Mean(nlos["highway"]), 4) + " vs roundabout " + Fmt(Mean(nlos["roundabout"]), 4)));
        }
    }
    return T.Finish();
}
