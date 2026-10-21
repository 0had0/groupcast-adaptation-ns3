// F03_sps_vs_dynamic (C: CORE vs SPSDYN, paired by cell / seed / arm): SB-SPS minus dynamic grants for PRR, leader collision rate, CBR
// and transmissions per packet.
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
    TestReport T("F03", "sps_vs_dynamic", "F");
    T.Summary("Paired differences SB-SPS minus dynamic (same cell, seed and arm): in background-free cells the scheduling mode is neutral; "
              "with background the leader collision rate is lower with SPS for periodic classes (directional).");
    T.AddRef("TS 38.214 8.1.4 (sensing: periodic reservations of others are excluded) [V]; TEST_PLAN F03");
    const auto core = LoadManifest("CORE");
    const auto dyn = LoadManifest("SPSDYN");
    if (core.empty() || dyn.empty())
    {
        T.Skip("CORE / SPSDYN data", "needs both manifests (campaign stage not run yet)");
        return T.Finish();
    }
    T.AddRunset("CORE");
    T.AddRunset("SPSDYN");
    struct K { double prr, ltc, cbr, tpp, collLoss; };
    std::map<std::string, K> sps;
    for (const auto& rr : core)
    {
        Run r;
        if (!r.Load(rr)) continue;
        sps[rr.scenario + "/" + rr.ivd + "/" + rr.bg + "/" + rr.traffic + "/" + rr.arm + "/s" + std::to_string(rr.seed)] = {r.R("prr_mean"), r.R("leader_tx_coll_rate"), r.R("cbr"), r.R("tx_per_pkt"), r.R("collision_loss_rate")};
    }
    // key by (bg) for grouping
    std::map<std::string, std::vector<double>> dPrr, dLtc, dCbr, dTpp, dColl; // group: bg/traffic
    CsvWriter w(TestsRoot() + "/results/F03_sps_vs_dynamic.csv", "scenario,ivd,bg,traffic,arm,seed,prr_sps,prr_dyn,ltc_sps,ltc_dyn,cbr_sps,cbr_dyn,txpp_sps,txpp_dyn");
    T.Artefact("results/F03_sps_vs_dynamic.csv");
    long pairs = 0;
    for (const auto& rr : dyn)
    {
        const std::string key = rr.scenario + "/" + rr.ivd + "/" + rr.bg + "/" + rr.traffic + "/" + rr.arm + "/s" + std::to_string(rr.seed);
        auto it = sps.find(key);
        if (it == sps.end()) continue;
        Run r;
        if (!r.Load(rr)) continue;
        ++pairs;
        T.AddNRuns(1);
        const K d{r.R("prr_mean"), r.R("leader_tx_coll_rate"), r.R("cbr"), r.R("tx_per_pkt"), r.R("collision_loss_rate")};
        const std::string g = (rr.bg == "none" ? "no background" : "background") + std::string("/") + rr.traffic;
        dPrr[g].push_back(it->second.prr - d.prr);
        dLtc[g].push_back(it->second.ltc - d.ltc);
        dCbr[g].push_back(it->second.cbr - d.cbr);
        dTpp[g].push_back(it->second.tpp - d.tpp);
        dColl[g].push_back(it->second.collLoss - d.collLoss);
        w.Row({rr.scenario, rr.ivd, rr.bg, rr.traffic, rr.arm, std::to_string(rr.seed), Fmt(it->second.prr, 6), Fmt(d.prr, 6), Fmt(it->second.ltc, 6), Fmt(d.ltc, 6), Fmt(it->second.cbr, 6), Fmt(d.cbr, 6), Fmt(it->second.tpp, 6), Fmt(d.tpp, 6)});
    }
    if (pairs == 0)
    {
        T.Skip("pairs", "no matching (cell, seed, arm) between CORE and SPSDYN");
        return T.Finish();
    }
    T.Info("paired (cell, seed, arm) comparisons", Opt().Obs(FmtI(pairs) + " pairs").Note("quick profile: hwy ivd30, bg {none, bgq}, periodic1tb, seeds 1-2, 6 arms; the full profile covers 3 traffic classes and 3 background levels"));
    for (const auto& [g, v] : dPrr)
    {
        auto line = [&](const char* name, const std::vector<double>& x) {
            const MeanCI m = MeanCi(x);
            return std::string(name) + " " + (m.ok() ? FmtCi(m.mean, m.lo, m.hi, 4) : Fmt(m.mean, 4)) + "; ";
        };
        T.Info(g + ": SPS - dynamic (n=" + FmtI(static_cast<long>(v.size())) + ")",
               Opt().Obs(line("dPRR", v) + line("dLeaderTxCollRate", dLtc[g]) + line("dCBR", dCbr[g]) + line("dTxPerPkt", dTpp[g])));
    }
    // directional: with background, SPS leader collision rate <= dynamic for periodic classes
    for (const auto& [g, v] : dLtc)
    {
        if (g.rfind("background/periodic", 0) != 0) continue;
        const MeanCI m = MeanCi(v);
        if (!m.ok()) { T.Info(g + ": SPS leader collision rate <= dynamic", Opt().Obs("fewer than 3 pairs")); continue; }
        if (m.mean <= 0) T.Pass(g + ": with background the leader collision rate with SPS is not higher than with dynamic grants", Opt().Exp("dLeaderTxCollRate <= 0").Obs(FmtCi(m.mean, m.lo, m.hi, 4)).Ref("TS 38.214 8.1.4 [V]"));
        else if (m.lo > 0) T.Info(g + ": SPS leader collision rate is HIGHER than dynamic (strongly violated expectation)", Opt().Exp("dLeaderTxCollRate <= 0").Obs(FmtCi(m.mean, m.lo, m.hi, 4)).Note("expectation from section 8 (F03) is directional only; reported as INFO"));
        else T.Info(g + ": SPS leader collision rate vs dynamic", Opt().Exp("dLeaderTxCollRate <= 0").Obs(FmtCi(m.mean, m.lo, m.hi, 4)).Note("CI includes 0: no verdict"));
    }
    return T.Finish();
}
