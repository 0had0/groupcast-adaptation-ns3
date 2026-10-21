// G03_spectrum_gain_prr_parity (C: CORE): measured from the raw logs (never from res_* columns): transmitted and reserved PSSCH
// subchannel-slots per generated packet and per delivered member-packet, PSFCH counts, gains against the blind counterpart with the same
// helper, PRR parity for the mean and the worst receivers; the error that F01 introduces in the existing results.
#include "common/analysis_kit.h"
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
struct K
{
    double txPerPkt{0}, txPerDelivered{0}, resPerPkt{0}, prrMean{0}, prrTail{0}, prrWorst{0}, psfchPerTb{0}, psfchRes{0}, usedPerPktCsv{0}, usedPerPktTrue{0};
    bool ok{false};
};
} // namespace

int
main()
{
    TestReport T("G03", "spectrum_gain_prr_parity", "G");
    T.Summary("Q8: Option 1 / Option 2 save transmitted (interference, energy, CBR) resources but, with Mode-2 reservations announced in SCI-1A, "
              "not reserved capacity; parity of the PRR against the blind counterpart for the mean, the tail and the empirically worst receiver.");
    T.AddRef("TEST_PLAN G03, D-2; TS 38.214 8.1.4 (reservations are announced in SCI-1A when the TB starts) [V]");
    CsvWriter outc(TestsRoot() + "/results/G03_summary.csv", "runset,group,arm,seeds,tx_per_pkt,tx_per_delivered,reserved_per_pkt,prr_mean,prr_tail,prr_worst,psfch_per_tb,psfch_resources_per_tb,res_per_pkt_used_csv,res_per_pkt_used_true");
    CsvWriter outg(TestsRoot() + "/results/G03_gains.csv", "runset,group,arm,blind_arm,tx_gain,tx_gain_lo,tx_gain_hi,res_gain,res_gain_lo,res_gain_hi,dprr_mean,dprr_mean_lo,dprr_mean_hi,dprr_tail,dprr_tail_lo,dprr_tail_hi,dprr_worst,dprr_worst_lo,dprr_worst_hi,n");
    CsvWriter f01(TestsRoot() + "/results/G03_f01_error_table.csv", "arm,n_runs,res_per_pkt_used_csv_mean,res_per_pkt_used_true_mean,relative_error");
    T.Artefact("results/G03_summary.csv");
    T.Artefact("results/G03_gains.csv");
    T.Artefact("results/G03_f01_error_table.csv");
    const std::vector<std::pair<std::string, std::string>> pairs = {{"R2", "R1"}, {"R3", "R1"}, {"R5_S1_H2", "R4_S1_H2"}, {"R6_S1_H2", "R4_S1_H2"}};
    std::map<std::string, std::vector<std::pair<double, double>>> f01Data;
    // F02: res_per_pkt_reserved of run.csv (closed-form estimate) against the reserved resources measured from the grant log
    std::map<std::string, std::vector<std::pair<double, double>>> f02Data;      // arm -> (csv, measured)
    std::map<std::string, std::map<std::string, double>> f02ByCell;             // cell|seed -> arm -> csv value (R4/R5/R6 only)
    for (const std::string rs : {"CORE", "SPSDYN"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { T.Skip(rs + " data", rs + " manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        // worst member per (cell, seed) from R1
        std::map<std::string, int> worstOf;
        for (const auto& rr : Select(all, "R1")) { Run r; if (r.Load(rr)) worstOf[rr.CellKey() + "|s" + std::to_string(rr.seed)] = WorstMemberByPrr(r); }
        std::map<std::string, std::map<std::string, K>> data;
        for (const auto& rr : all)
        {
            Run r;
            if (!r.Load(rr) || !r.hasAlltx || !r.hasGrants) continue;
            T.AddNRuns(1);
            const SpectrumAcct a = ComputeSpectrum(r);
            if (!a.ok) continue;
            const double pk = r.R("pkts");
            if (pk <= 0) continue;
            K k;
            k.ok = true;
            k.txPerPkt = (a.txLeader + a.txHelper) / pk;
            double delivered = 0;
            for (int m = 1; m <= kMembers; ++m) delivered += r.MemberCol("rx", m);
            k.txPerDelivered = delivered > 0 ? (a.txLeader + a.txHelper) / delivered : NAN;
            k.resPerPkt = a.res.reserved / pk;
            k.prrMean = r.R("prr_mean");
            k.prrTail = r.MemberCol("prr_pdb", kMembers);
            const std::string key = rr.CellKey() + "|s" + std::to_string(rr.seed);
            const int w = worstOf.count(key) ? worstOf[key] : kMembers;
            k.prrWorst = r.MemberCol("prr_pdb", w);
            const double tbs = std::max(1.0, r.NewTbsInWindow() * 1.0);
            if (rr.HarqMode() == "opt1") { k.psfchPerTb = r.R("nack_rx") / tbs; k.psfchRes = 1.0; }
            else if (rr.HarqMode() == "opt2") { k.psfchPerTb = (r.R("nack_rx") + r.R("explicit_ack_rx")) / tbs; k.psfchRes = rr.IsHelperArm() ? 7.0 : 8.0; }
            k.usedPerPktCsv = r.R("res_per_pkt_used");
            k.usedPerPktTrue = k.txPerPkt;
            f01Data[rr.arm].push_back({k.usedPerPktCsv, k.usedPerPktTrue});
            f02Data[rr.arm].push_back({r.R("res_per_pkt_reserved"), k.resPerPkt});
            if (rr.IsHelperArm()) f02ByCell[rs + "|" + key][rr.arm] = r.R("res_per_pkt_reserved");
            data[rr.CellKey() + "|" + rr.arm][std::to_string(rr.seed)] = k;
        }
        // summary per (cell, arm)
        std::map<std::string, std::map<std::string, std::vector<K>>> byCell;
        for (const auto& [ca, bySeed] : data)
        {
            const std::string cell = ca.substr(0, ca.rfind('|')), arm = ca.substr(ca.rfind('|') + 1);
            for (const auto& [s, k] : bySeed) byCell[cell][arm].push_back(k);
        }
        for (const auto& [cell, byArm] : byCell)
            for (const auto& [arm, v] : byArm)
            {
                auto mean = [&](double K::*f) { std::vector<double> x; for (const auto& k : v) x.push_back(k.*f); return Mean(x); };
                outc.Row({rs, cell, arm, std::to_string(v.size()), Fmt(mean(&K::txPerPkt), 6), Fmt(mean(&K::txPerDelivered), 6), Fmt(mean(&K::resPerPkt), 6), Fmt(mean(&K::prrMean), 6), Fmt(mean(&K::prrTail), 6),
                          Fmt(mean(&K::prrWorst), 6), Fmt(mean(&K::psfchPerTb), 6), Fmt(mean(&K::psfchRes), 3), Fmt(mean(&K::usedPerPktCsv), 6), Fmt(mean(&K::usedPerPktTrue), 6)});
            }
        // gains vs blind counterpart
        for (const auto& [h, b] : pairs)
            for (const auto& [cell, byArm] : byCell)
            {
                if (!byArm.count(h) || !byArm.count(b)) continue;
                // seeds in common
                std::vector<double> gTx, gRes, dm, dt, dw;
                const std::string base = cell;
                for (const auto& [ca, bySeed] : data)
                {
                    (void)ca;
                    break;
                }
                for (const auto& [s, kh] : data[cell + "|" + h])
                {
                    auto it = data[cell + "|" + b].find(s);
                    if (it == data[cell + "|" + b].end()) continue;
                    const K& kb = it->second;
                    gTx.push_back(kb.txPerPkt > 0 ? 1.0 - kh.txPerPkt / kb.txPerPkt : 0.0);
                    gRes.push_back(kb.resPerPkt > 0 ? 1.0 - kh.resPerPkt / kb.resPerPkt : 0.0);
                    dm.push_back(kh.prrMean - kb.prrMean);
                    dt.push_back(kh.prrTail - kb.prrTail);
                    dw.push_back(kh.prrWorst - kb.prrWorst);
                }
                if (gTx.size() < 2) continue;
                const MeanCI a = MeanCi(gTx), c = MeanCi(gRes), d1 = MeanCi(dm), d2 = MeanCi(dt), d3 = MeanCi(dw);
                outg.Row({rs, cell, h, b, Fmt(a.mean, 6), Fmt(a.lo, 6), Fmt(a.hi, 6), Fmt(c.mean, 6), Fmt(c.lo, 6), Fmt(c.hi, 6), Fmt(d1.mean, 6), Fmt(d1.lo, 6), Fmt(d1.hi, 6), Fmt(d2.mean, 6), Fmt(d2.lo, 6), Fmt(d2.hi, 6), Fmt(d3.mean, 6), Fmt(d3.lo, 6), Fmt(d3.hi, 6), std::to_string(gTx.size())});
                const std::string pre = rs + " " + cell + ": " + h + " vs " + b + ": ";
                if (gTx.size() < 3)
                {
                    T.Info(pre + "gains and parity", Opt().Obs(std::to_string(gTx.size()) + " seeds: fewer than 3, no verdict. transmitted gain " + Fmt(a.mean, 4) + ", reserved gain " + Fmt(c.mean, 4) + ", dPRR_mean " + Fmt(d1.mean, 4) + ", dPRR_tail " + Fmt(d2.mean, 4) + ", dPRR_worst " + Fmt(d3.mean, 4)));
                    continue;
                }
                T.Info(pre + "transmitted gain / reserved gain (1 - ratio to the blind arm)", Opt().Obs("transmitted " + FmtCi(a.mean, a.lo, a.hi, 4) + ", reserved " + FmtCi(c.mean, c.lo, c.hi, 4)).Note("Q3: transmitted resources are saved, reserved capacity stays (reservations are announced when the TB starts)"));
                const bool parity = d3.IncludesZero() && std::fabs(d3.mean) <= 0.01;
                T.Info(pre + (parity ? "same PRR achievable (worst receiver)" : "PRR gap to the blind arm"),
                       Opt().Obs("dPRR_mean " + FmtCi(d1.mean, d1.lo, d1.hi, 4) + ", dPRR_tail " + FmtCi(d2.mean, d2.lo, d2.hi, 4) + ", dPRR_worst " + FmtCi(d3.mean, d3.lo, d3.hi, 4))
                           .Note("verdict rule: the CI of dPRR_worst includes 0 and |mean| <= 0.01"));
            }
    }
    // F01 error table before the fix (tests/out_prefix_v1)
    {
        CsvWriter f01v1(TestsRoot() + "/results/G03_f01_error_table_v1.csv", "arm,n_runs,res_per_pkt_used_csv_mean,res_per_pkt_used_true_mean,relative_error");
        T.Artefact("results/G03_f01_error_table_v1.csv");
        std::map<std::string, std::vector<std::pair<double, double>>> v1Data;
        for (const std::string rs : {"CORE", "SPSDYN"})
        {
            for (const auto& rr : LoadManifest(rs, OutDirV1()))
            {
                Run r;
                if (!r.Load(rr) || !r.hasAlltx || !r.hasGrants) continue;
                const SpectrumAcct a = ComputeSpectrum(r);
                const double pk = r.R("pkts");
                if (!a.ok || pk <= 0) continue;
                v1Data[rr.arm].push_back({r.R("res_per_pkt_used"), (a.txLeader + a.txHelper) / pk});
            }
        }
        for (const auto& [arm, v] : v1Data)
        {
            double c = 0, t = 0;
            for (const auto& p : v) { c += p.first; t += p.second; }
            c /= v.size(); t /= v.size();
            f01v1.Row({arm, std::to_string(v.size()), Fmt(c, 6), Fmt(t, 6), Fmt(t > 0 ? (c - t) / t : 0, 5)});
            T.Info("F01 before the fix (reference tests/out_prefix_v1): res_per_pkt_used " + arm, Opt().Fnd("F01").Obs("csv " + Fmt(c, 5) + " vs measured " + Fmt(t, 5) + " (" + Fmt(t > 0 ? 100.0 * (c - t) / t : 0, 4) + " %) over " + std::to_string(v.size()) + " runs"));
        }
    }
    // F01 error table
    for (const auto& [arm, v] : f01Data)
    {
        double c = 0, t = 0;
        for (const auto& p : v) { c += p.first; t += p.second; }
        c /= v.size(); t /= v.size();
        f01.Row({arm, std::to_string(v.size()), Fmt(c, 6), Fmt(t, 6), Fmt(t > 0 ? (c - t) / t : 0, 5)});
        T.Check("F01 (fixed): res_per_pkt_used of run.csv equals the measured resources per packet: " + arm, std::fabs(t > 0 ? (c - t) / t : 0.0) < 1e-4,
                Opt().Fnd("F01").Sev("H").Exp("relative error < 1e-4").Obs("csv " + Fmt(c, 5) + " vs measured " + Fmt(t, 5) + " (" + Fmt(t > 0 ? 100.0 * (c - t) / t : 0, 4) + " %) over " + std::to_string(v.size()) + " runs").Tol("1e-4")
                    .Note("after TEST_PLAN 0.2: res_used_helper sums the PSSCH of members 1..N"));
    }
    // F02
    {
        long same = 0, cells = 0;
        for (const auto& [cell, byArm] : f02ByCell)
        {
            if (byArm.size() < 3) continue;
            ++cells;
            const double a = byArm.at("R4_S1_H2"), b = byArm.at("R5_S1_H2"), c = byArm.at("R6_S1_H2");
            same += (std::fabs(a - b) < 1e-9 && std::fabs(b - c) < 1e-9) ? 1 : 0;
        }
        for (const auto& [arm, v] : f02Data)
        {
            double c = 0, t = 0;
            for (const auto& p : v) { c += p.first; t += p.second; }
            c /= v.size(); t /= v.size();
            T.Info("F02 res_per_pkt_reserved of run.csv vs reserved subchannel-slots per packet measured from the grant log: " + arm, Opt().Fnd("F02")
                       .Obs("csv " + Fmt(c, 5) + " vs measured " + Fmt(t, 5) + " (" + Fmt(t > 0 ? 100.0 * (c - t) / t : 0, 4) + " %) over " + std::to_string(v.size()) + " runs"));
        }
        if (cells > 0)
            T.CheckXfail("F02: res_per_pkt_reserved differs between R4, R5 and R6 of the same cell and seed (a measurement would, a closed-form estimate does not)", 2 * same <= cells,
                         Opt().Fnd("F02").Sev("H").Exp("values differ in most cells").Obs(std::to_string(same) + " of " + std::to_string(cells) + " (cell, seed) groups have identical res_per_pkt_reserved in R4, R5 and R6")
                             .Note("res_reserved_* / res_unused_* are closed-form estimates (main.cc:3997-4020); this suite uses the grant-log measurement of spectrum_accounting instead (D-2)"));
    }
    return T.Finish();
}
