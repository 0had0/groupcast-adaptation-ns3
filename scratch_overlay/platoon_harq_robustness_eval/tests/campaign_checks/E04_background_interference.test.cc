// E04_background_interference (C: CORE): with more background traffic the CBR rises, collisions rise and the PRR falls (paired over
// seeds and traffic classes); at no background the CBR is exactly the platoon's own share.
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/spectrum_accounting.h"
#include "common/stats.h"

#include <cmath>
#include <map>

using namespace ptest;

namespace
{
double
BgLoad(const std::string& bg)
{
    // offered flows: cars x flows per car (run_matrix BG_LEVELS)
    if (bg == "none") return 0;
    if (bg == "bgq") return 15 * 1.67;
    if (bg == "bgq2") return 15 * 3.33;
    if (bg == "moderate") return 60 * 1.67;
    if (bg == "high") return 60 * 3.33;
    return -1;
}
} // namespace

int
main()
{
    TestReport T("E04", "background_interference", "E");
    T.Summary("More background traffic -> higher CBR (strict, every pair), more collision losses, lower PRR; at no background the CBR "
              "equals the platoon's own transmitted share.");
    T.AddRef("TEST_PLAN E04; main.cc:2689-2722 (cbr)");
    ViolationLog V;
    const auto all = LoadManifest("CORE");
    if (all.empty())
    {
        T.Skip("CORE data", "CORE manifest not present (campaign stage not run yet)");
        return T.Finish();
    }
    T.AddRunset("CORE");

    struct Kpi { double cbr, coll, prr, ltc, loss; };
    // key: scenario/ivd/traffic/arm/seed -> bg -> kpi
    std::map<std::string, std::map<std::string, Kpi>> data;
    long ownBad = 0, ownN = 0;
    CsvWriter w(TestsRoot() + "/results/E04_prr_coll_vs_bg.csv", "scenario,ivd,traffic,arm,seed,bg,bg_load_flows,cbr,collision_loss_rate,prr_mean,leader_tx_coll_rate");
    T.Artefact("results/E04_prr_coll_vs_bg.csv");
    for (const auto& rr : all)
    {
        Run r;
        if (!r.Load(rr)) continue;
        T.AddNRuns(1);
        const Kpi k{r.R("cbr"), r.R("collision_loss_rate"), r.R("prr_mean"), r.R("leader_tx_coll_rate"), 0};
        data[rr.scenario + "/" + rr.ivd + "/" + rr.traffic + "/" + rr.arm + "/s" + std::to_string(rr.seed)][rr.bg] = k;
        w.Row({rr.scenario, rr.ivd, rr.traffic, rr.arm, std::to_string(rr.seed), rr.bg, Fmt(BgLoad(rr.bg), 5), Fmt(k.cbr, 7), Fmt(k.coll, 7), Fmt(k.prr, 7), Fmt(k.ltc, 7)});
        if (rr.bg == "none" && r.hasAlltx && r.hasGrants)
        {
            const SpectrumAcct sp = ComputeSpectrum(r);
            if (sp.ok)
            {
                const double own = (sp.txLeader + sp.txHelper) / ((r.kpiEnd - r.kpiStart) / kSlotS * r.R("n_subch", 5));
                ++ownN;
                if (std::fabs(own - k.cbr) > 1e-5)
                {
                    ++ownBad;
                    V.Add("cbr(no background) == platoon own share", rr.arm + "/" + rr.tag + " cbr " + Fmt(k.cbr, 7) + " own " + Fmt(own, 7));
                }
            }
        }
    }
    if (ownN > 0)
    {
        T.Check("no background: cbr equals the platoon's own transmitted share (leader + helper subchannel-slots / window slots)", ownBad == 0,
                Opt().Exp("0 of " + FmtI(ownN)).Obs(FmtI(ownBad)).Tol("1e-5").Sev("M"));
    }
    // paired level comparisons
    const std::vector<std::pair<std::string, std::string>> steps = {{"none", "bgq"}, {"bgq", "bgq2"}, {"none", "bgq2"}, {"none", "moderate"}, {"moderate", "high"}, {"none", "high"}};
    for (const auto& [lo, hi] : steps)
    {
        std::map<std::string, std::vector<double>> dCbr, dColl, dPrr, dLtc; // arm -> deltas
        long strictBad = 0, pairs = 0;
        for (const auto& [key, byBg] : data)
        {
            auto a = byBg.find(lo), b = byBg.find(hi);
            if (a == byBg.end() || b == byBg.end()) continue;
            const std::string arm = key.substr(0, key.rfind("/s"));
            const std::string armOnly = arm.substr(arm.rfind('/') + 1);
            ++pairs;
            dCbr[armOnly].push_back(b->second.cbr - a->second.cbr);
            dColl[armOnly].push_back(b->second.coll - a->second.coll);
            dPrr[armOnly].push_back(b->second.prr - a->second.prr);
            dLtc[armOnly].push_back(b->second.ltc - a->second.ltc);
            if (!(b->second.cbr > a->second.cbr))
            {
                ++strictBad;
                V.Add("cbr strictly increases with background", key + " " + lo + "->" + hi + " " + Fmt(a->second.cbr, 7) + " -> " + Fmt(b->second.cbr, 7));
            }
        }
        if (pairs == 0) continue;
        const std::string pre = lo + " -> " + hi + ": ";
        T.Check(pre + "cbr increases in every (cell, seed) pair", strictBad == 0, Opt().Exp("0 of " + FmtI(pairs) + " pairs without an increase").Obs(FmtI(strictBad)).Tol("0").Sev("M"));
        auto directional = [&](const std::string& what, std::map<std::string, std::vector<double>>& d, int sign) {
            std::vector<double> allv;
            for (auto& [arm, v] : d) allv.insert(allv.end(), v.begin(), v.end());
            const MeanCI m = MeanCi(allv);
            if (!m.ok())
            {
                T.Info(pre + what, Opt().Obs(FmtI(static_cast<long>(allv.size())) + " pairs: fewer than 3, no verdict (section 7.3)"));
                return;
            }
            const bool okSign = sign > 0 ? m.mean >= 0 : m.mean <= 0;
            const bool significantWrong = sign > 0 ? m.hi < 0 : m.lo > 0;
            std::string perArm;
            for (auto& [arm, v] : d) perArm += arm + " " + Fmt(Mean(v), 3) + "; ";
            if (okSign) T.Pass(pre + what, Opt().Obs("mean difference " + FmtCi(m.mean, m.lo, m.hi, 4) + " (n=" + FmtI(static_cast<long>(allv.size())) + ")  per arm: " + perArm).Exp(sign > 0 ? "increase" : "decrease"));
            else if (significantWrong) T.Fail(pre + what, Opt().Obs("mean difference " + FmtCi(m.mean, m.lo, m.hi, 4) + "  per arm: " + perArm).Exp(sign > 0 ? "increase" : "decrease").Sev("M"));
            else T.Info(pre + what, Opt().Obs("mean difference " + FmtCi(m.mean, m.lo, m.hi, 4) + "  per arm: " + perArm).Exp(sign > 0 ? "increase" : "decrease").Note("wrong sign but the CI includes 0: statistical power (section 7.3)"));
        };
        directional("collision_loss_rate is non-decreasing", dColl, +1);
        directional("prr_mean is non-increasing", dPrr, -1);
        directional("leader_tx_coll_rate is non-decreasing", dLtc, +1);
    }
    const std::string art = V.Write("E04_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
