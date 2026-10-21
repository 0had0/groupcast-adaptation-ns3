// D01_kuo_objective (U, links ../helper_select.cc): independent re-implementation of Kuo et al. 2021 eq. 12-13 with the
// documented link model (TR 37.885 PL, shadowing, NLOSv blockage integral, distance-only P_LOS) compared with
// HelperSelector over 200 random platoon geometries; selector properties; H4 note (F10); F09 (vehicle blockage ignored).
#include "common/harness.h"
#include "common/kuo_model.h"

#include "helper_select.h"

#include "ns3/constant-position-mobility-model.h"
#include "ns3/core-module.h"
#include "ns3/mobility-module.h"
#include "ns3/node-container.h"

#include <cmath>
#include <random>
#include <regex>

using namespace ns3;
using ptest::Opt;
using ptest::TestReport;

namespace
{

using namespace ptest::kuo;

NodeContainer
MakeNodes(const std::vector<Vector>& pos)
{
    NodeContainer nodes;
    nodes.Create(pos.size());
    for (size_t i = 0; i < pos.size(); ++i)
    {
        Ptr<ConstantPositionMobilityModel> m = CreateObject<ConstantPositionMobilityModel>();
        m->SetPosition(pos[i]);
        nodes.Get(i)->AggregateObject(m);
    }
    return nodes;
}

struct Run1
{
    uint32_t applied{0}, chosen{0};
    std::vector<float> score;
};

Run1
RunSelector(const std::vector<Vector>& pos, const HelperSelector::Config& cfg, uint32_t nSubch)
{
    NodeContainer nodes = MakeNodes(pos);
    HelperSelector sel;
    sel.Init(cfg, nodes, 8, nullptr);
    Simulator::Stop(Seconds(0.2));
    Simulator::Run();
    Run1 r;
    r.applied = sel.Choose(0.1, 0.1, 0, nSubch);
    r.chosen = sel.Rows().back().chosen;
    r.score = sel.Rows().back().score;
    Simulator::Destroy();
    return r;
}

} // namespace

int
main()
{
    TestReport T("D01", "kuo_objective", "D");
    T.Summary("HelperSelector reproduces an independent implementation of Kuo et al. 2021 eq. 12-13 (200 random "
              "geometries); selector properties hold; H2 ignores vehicle blockage (F09).");
    T.AddRef("README Kuo eq. 12-13 (custom helper design, TEST_PLAN section 5)");
    T.AddRef("Kuo et al., IEEE Access 2021, eq. 7, 12, 13; 3GPP TR 37.885 V2V path loss / P_LOS");

    std::mt19937 rng(20261017);
    std::uniform_real_distribution<double> uSp(5.0, 80.0);
    std::uniform_int_distribution<int> uSub(1, 3);

    // ---- 1. 200 random geometries, both scenario parameter sets ---------------------------------------------------
    for (const std::string scen : {"highway", "roundabout"})
    {
        const bool hw = (scen == "highway");
        double maxDiff = 0;
        long agree = 0, total = 0, tieCases = 0;
        std::vector<long> chosenHist(9, 0);
        for (int c = 0; c < 200; ++c)
        {
            const double sp = uSp(rng);
            const uint32_t nSub = uSub(rng);
            std::vector<Vector> pos;
            for (int i = 0; i < 9; ++i)
            {
                pos.emplace_back(i * sp, 0.0, 1.6);
            }
            HelperSelector::Config cfg;
            cfg.policy = "kuo";
            cfg.fallback = 4;
            cfg.scenario = scen;
            cfg.subchPrbs = 10;
            Run1 r = RunSelector(pos, cfg, nSub);
            std::vector<double> dL(9, 0.0);
            std::vector<std::vector<double>> dM(9, std::vector<double>(9, 0.0));
            for (int a = 0; a <= 8; ++a)
            {
                dL[a] = std::fabs(pos[a].x - pos[0].x);
                for (int b = 0; b <= 8; ++b)
                {
                    dM[a][b] = std::fabs(pos[a].x - pos[b].x);
                }
            }
            const Scores s = KuoScores(dL, dM, hw, nSub, false);
            for (int i = 0; i < 8; ++i)
            {
                maxDiff = std::max(maxDiff, std::fabs(static_cast<double>(r.score[i]) - s.g[i]));
            }
            ++total;
            if (r.chosen == s.chosen)
            {
                ++agree;
            }
            else
            {
                // a disagreement is only acceptable when the two candidates tie within the score tolerance
                const double a = s.g[r.chosen - 1], b = s.g[s.chosen - 1];
                if (std::fabs(a - b) < 1e-3)
                {
                    ++tieCases;
                    ++agree;
                }
            }
            chosenHist[r.chosen] += 1;
        }
        T.Check("kuo/" + scen + ": max |score_selector - score_independent| < 1e-3 over 200 geometries", maxDiff < 1e-3,
                Opt().Exp("< 1e-3").Obs(ptest::Fmt(maxDiff, 3)).Tol("1e-3").Sev("H")
                    .Note("independent implementation integrates the NLOSv blockage with 4000-interval Simpson; selector uses an 80-point midpoint rule"));
        T.Check("kuo/" + scen + ": chosen == independent argmax (ties within 1e-3 allowed)", agree == total,
                Opt().Exp(ptest::FmtI(total) + " agree").Obs(ptest::FmtI(agree) + " agree, " + ptest::FmtI(tieCases) +
                                                              " near-ties resolved differently").Sev("H"));
        std::string hist;
        for (int m = 1; m <= 8; ++m)
        {
            hist += "m" + std::to_string(m) + "=" + ptest::FmtI(chosenHist[m]) + " ";
        }
        T.Info("kuo/" + scen + ": chosen-member histogram over the 200 geometries", Opt().Obs(hist));
    }

    // ---- 2. properties --------------------------------------------------------------------------------------------
    {
        // leader never chosen, scores only for members 1..8, chosen in [1,8]
        bool ok = true;
        for (int c = 0; c < 50; ++c)
        {
            const double sp = uSp(rng);
            std::vector<Vector> pos;
            for (int i = 0; i < 9; ++i)
            {
                pos.emplace_back(i * sp, 0.0, 1.6);
            }
            HelperSelector::Config cfg;
            cfg.policy = "kuo";
            cfg.scenario = "highway";
            Run1 r = RunSelector(pos, cfg, 2);
            ok = ok && r.chosen >= 1 && r.chosen <= 8 && r.score.size() == 8;
        }
        T.Check("prop/the leader is never chosen (chosen in 1..8, 8 candidate scores)", ok, Opt().Sev("M"));
    }
    {
        // monotone in pRelay: pResource 0.5 vs 1.0; min <= mean
        bool mono = true, minLeMean = true;
        for (int c = 0; c < 50; ++c)
        {
            const double sp = uSp(rng);
            std::vector<Vector> pos;
            for (int i = 0; i < 9; ++i)
            {
                pos.emplace_back(i * sp, 0.0, 1.6);
            }
            HelperSelector::Config c1, c2, c3;
            c1.policy = c2.policy = c3.policy = "kuo";
            c1.scenario = c2.scenario = c3.scenario = "highway";
            c1.pResource = 1.0;
            c2.pResource = 0.5;
            c3.kuoObjective = "min";
            Run1 a = RunSelector(pos, c1, 2), b = RunSelector(pos, c2, 2), m = RunSelector(pos, c3, 2);
            for (int i = 0; i < 8; ++i)
            {
                mono = mono && (a.score[i] + 1e-6 >= b.score[i]);
                minLeMean = minLeMean && (m.score[i] <= a.score[i] + 1e-6);
            }
        }
        T.Check("prop/score non-decreasing in the relay link probability (pResource 0.5 -> 1.0)", mono,
                Opt().Exp("score(1.0) >= score(0.5) for all candidates").Sev("M"));
        T.Check("prop/kuoObjective=min value <= the mean for every candidate", minLeMean, Opt().Sev("M"));
        // non-decreasing in pL[i] (analytic property of eq. 12: d success_r / d pL[i] = (1-pL[r]) pRelay >= 0)
        bool derivOk = true;
        for (double pl = 0.05; pl < 0.95; pl += 0.05)
        {
            for (double pr = 0.05; pr < 0.95; pr += 0.15)
            {
                const double a = 0.4 + (1 - 0.4) * pl * pr;
                const double b = 0.4 + (1 - 0.4) * (pl + 0.05) * pr;
                derivOk = derivOk && b >= a;
            }
        }
        T.Check("prop/eq. 12 term pL[r] + (1-pL[r]) pL[i] pRelay is non-decreasing in pL[i] and pRelay", derivOk,
                Opt().Note("algebraic property of the formula, checked numerically on a grid"));
    }
    {
        // static policy returns the fallback, in every position
        bool ok = true;
        for (uint32_t fb = 1; fb <= 8; ++fb)
        {
            std::vector<Vector> pos;
            for (int i = 0; i < 9; ++i)
            {
                pos.emplace_back(i * 30.0, 0.0, 1.6);
            }
            HelperSelector::Config cfg;
            cfg.policy = "static";
            cfg.fallback = fb;
            Run1 r = RunSelector(pos, cfg, 2);
            ok = ok && r.applied == fb && r.chosen == fb;
        }
        T.Check("prop/static policy (H1 mode of the selector) always returns the fallback", ok,
                Opt().Note("property test of the selector only; no H1 simulation is run (decision D-1)"));
    }
    {
        // shadow mode: applied == fallback, chosen == the non-shadow choice
        bool ok = true;
        for (int c = 0; c < 30; ++c)
        {
            const double sp = uSp(rng);
            std::vector<Vector> pos;
            for (int i = 0; i < 9; ++i)
            {
                pos.emplace_back(i * sp, 0.0, 1.6);
            }
            HelperSelector::Config a, b;
            a.policy = b.policy = "kuo";
            a.scenario = b.scenario = "highway";
            a.fallback = b.fallback = 4;
            b.shadow = true;
            Run1 ra = RunSelector(pos, a, 2), rb = RunSelector(pos, b, 2);
            ok = ok && rb.applied == 4 && rb.chosen == ra.chosen && ra.applied == ra.chosen;
        }
        T.Check("prop/shadow mode applies the fallback and logs the same choice", ok, Opt().Sev("M"));
    }

    // ---- 3. F09: H2 ignores vehicle blockage -----------------------------------------------------------------------
    {
        // zig-zag platoon (members +-10 m off the axis): no vehicle lies on a link, so the geometry-aware 'map' policy sees
        // every link as clear; then a background vehicle is placed on the leader -> member 6 segment
        std::vector<Vector> pos;
        for (int i = 0; i < 9; ++i)
        {
            pos.emplace_back(i * 30.0, i == 0 ? 0.0 : ((i % 2) ? 10.0 : -10.0), 1.6);
        }
        std::vector<Vector> posB = pos;
        posB.emplace_back(0.5 * pos[6].x, 0.5 * pos[6].y, 1.6); // blocker at the midpoint of leader -> member 6
        HelperSelector::Config kuo, map;
        kuo.policy = "kuo";
        kuo.scenario = "highway";
        map.policy = "map";
        map.scenario = "highway";
        Run1 k0 = RunSelector(pos, kuo, 2), k1 = RunSelector(posB, kuo, 2);
        Run1 m0 = RunSelector(pos, map, 2), m1 = RunSelector(posB, map, 2);
        double dk = 0, dm = 0;
        for (int i = 0; i < 8; ++i)
        {
            dk = std::max(dk, static_cast<double>(std::fabs(k0.score[i] - k1.score[i])));
            dm = std::max(dm, static_cast<double>(std::fabs(m0.score[i] - m1.score[i])));
        }
        T.Check("F09/control: the geometry-aware 'map' policy does react to a vehicle on a link (the test geometry is sensitive)", dm > 1e-9,
                Opt().Exp("max |score change| > 0").Obs(ptest::Fmt(dm, 3)).Sev("L"));
        T.CheckXfail("F09/kuo scores react to a vehicle on a link", dk > 1e-9,
                     Opt().Fnd("F09").Sev("M")
                         .Exp("blockage-aware scores (the channel applies NLOSv to blocked in-platoon links)")
                         .Obs("kuo max |Δscore| = " + ptest::Fmt(dk, 3) + "; the geometry-aware 'map' policy changes by " +
                              ptest::Fmt(dm, 3))
                         .Note("H2 (Kuo) is distance-only: P_LOS(d) mixture without the vehicle boxes that the simulator's "
                               "geometric channel turns into NLOSv. In a straight platoon the choice is therefore nearly static "
                               "(see the chosen-member histogram above and D02)."));
    }

    // ---- 4. F10: H4 treats members with no data as reliable ----------------------------------------------------------
    {
        std::vector<Vector> pos;
        for (int i = 0; i < 9; ++i)
        {
            pos.emplace_back(i * 30.0, 0.0, 1.6);
        }
        NodeContainer nodes = MakeNodes(pos);
        HelperSelector sel;
        HelperSelector::Config cfg;
        cfg.policy = "harq";
        cfg.hstatRule = "nearestReliable";
        cfg.hstatCandMaxFail = 0.5;
        cfg.fallback = 4;
        sel.Init(cfg, nodes, 8, nullptr);
        Simulator::Stop(Seconds(0.2));
        Simulator::Run();
        for (uint32_t tb = 1; tb <= 10; ++tb)
        {
            sel.OnFeedback(0.05, 6, tb, 1); // member 6 always fails (NACK)
            sel.OnFeedback(0.05, 5, tb, 1); // member 5: NACK, candidate neighbour that is unreliable
            sel.OnFeedback(0.05, 4, tb, 1);
        }
        const uint32_t chosen = sel.Choose(0.1, 0.1, 0, 2);
        Simulator::Destroy();
        T.Info("F10/H4 (harq, nearestReliable) with feedback for members 4-6 only: chosen member " + std::to_string(chosen),
               Opt().Fnd("F10")
                   .Obs("members 1-3, 7, 8 have no feedback and count as failure rate 0 (reliable) in failRate(); worst member = 6")
                   .Note("H4 is not in the campaign; shown for the record (helper_select.cc:384-386)"));
    }

    // ---- 5. the existing unit binary --------------------------------------------------------------------------------
    {
        const std::string bin = ptest::RepoRoot() +
                                "/ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3.42-test_helper_select-default";
        if (!ptest::FileExists(bin))
        {
            T.Skip("existing/test_helper_select", "binary not built");
        }
        else
        {
            int rc = 0;
            const std::string out = ptest::Shell("'" + bin + "' 2>&1 | tail -3", &rc);
            std::smatch m;
            long pass = -1, fail = -1;
            if (std::regex_search(out, m, std::regex("Results: ([0-9]+) passed, ([0-9]+) failed")))
            {
                pass = std::stol(m[1]);
                fail = std::stol(m[2]);
            }
            T.Check("existing/test_helper_select passes", fail == 0 && pass > 0,
                    Opt().Exp("0 failed").Obs(ptest::FmtI(pass) + " passed, " + ptest::FmtI(fail) + " failed"));
            T.Metric("test_helper_select_pass_count", static_cast<double>(pass));
        }
    }

    return T.Finish();
}
