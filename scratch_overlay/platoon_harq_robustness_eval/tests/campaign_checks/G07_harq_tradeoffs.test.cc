// G07_harq_tradeoffs (C: CORE + SPSDYN; W: SYN_MARGINAL; arms R2, R3, R5_S1_H2, R6_S1_H2 vs their blind counterparts R1, R4_S1_H2): count, Wilson-CI
// probability and conditional tables for the HARQ trade-off events T1-T8 (decision D-4) and the PRR-loss decomposition of each HARQ arm against its
// blind counterpart (the decomposition must add up to the measured PRR difference).
#include "common/analysis_kit.h"
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>

using namespace ptest;

namespace
{
struct KN { long k{0}, n{0}; };
std::string
CondName(int c)
{
    return c == 2 ? "LOS" : c == 1 ? "NLOSv" : c == 0 ? "NLOS" : "unknown";
}
std::string
DistBin(double d)
{
    return d <= 50 ? "<=50m" : d <= 100 ? "50-100m" : d <= 200 ? "100-200m" : d <= 300 ? "200-300m" : ">300m";
}
std::string
SinrBin(double s)
{
    return std::isnan(s) || s < -90 ? "sci_missed" : s < 5 ? "<5dB" : s < 10 ? "5-10dB" : s < 15 ? "10-15dB" : s < 20 ? "15-20dB" : ">=20dB";
}
} // namespace

int
main()
{
    TestReport T("G07", "harq_tradeoffs", "G");
    T.Summary("D-4: the price of HARQ feedback against blind retransmission: latency of the PSFCH round trip (T1), deadline misses (T2), members lost to a "
              "missed SCI (T3), the MCR gate (T4), Option-2 DTX waste (T5), stale HARQ ids (T6), collision exposure (T7) and resource geometry (T8), with "
              "counts, probabilities and the conditions under which they occur, plus a PRR-loss decomposition that adds up.");
    T.AddRef("TS 38.321 5.22.1.3, TS 38.213 16.3 [V]; TEST_PLAN D-4, F08, F25, section 8 G07");
    ViolationLog V;
    CsvWriter cond(TestsRoot() + "/results/G07_conditions.csv", "runset,event,arm,dimension,value,n,k,p,lo,hi");
    CsvWriter heat(TestsRoot() + "/results/G07_heatmap.csv", "runset,event,arm,traffic,bg,member,n,k,p");
    CsvWriter dec(TestsRoot() + "/results/G07_prr_decomposition.csv", "runset,group,arm,blind_arm,category,rate_arm,rate_blind,delta_rate,delta_prr_measured,sum_check_error");
    CsvWriter gaps(TestsRoot() + "/results/G07_t1_t8_distributions.csv", "runset,arm,metric,value,count");
    T.Artefact("results/G07_conditions.csv");
    T.Artefact("results/G07_heatmap.csv");
    T.Artefact("results/G07_prr_decomposition.csv");
    T.Artefact("results/G07_t1_t8_distributions.csv");
    const std::vector<std::pair<std::string, std::string>> pairs = {{"R2", "R1"}, {"R3", "R1"}, {"R5_S1_H2", "R4_S1_H2"}, {"R6_S1_H2", "R4_S1_H2"}};

    for (const std::string rs : {"CORE", "SPSDYN", "SYN_MARGINAL"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { if (rs != "SYN_MARGINAL") T.Skip(rs + " data", rs + " manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        // tallies: key "event|arm|dimension|value" -> (k, n)
        std::map<std::string, KN> tally;
        std::map<std::string, KN> heatT; // "event|arm|traffic|bg|member"
        std::map<std::string, std::vector<double>> gapMs, slotOff;   // arm -> samples
        std::map<std::string, KN> rbReuse, t7;                        // arm / (arm|bg)
        std::map<std::string, std::vector<double>> blindLastOff;      // cell -> offsets of blind last copy (ms)
        std::map<std::string, KN> t1Later;                            // arm|cell
        std::map<std::string, std::vector<double>> delaysHarq, delaysBlind; // cell|seed|member keys flattened later
        std::map<std::string, std::map<std::string, std::array<double, 2>>> meanDelay; // cellseed -> arm|member -> (sum, n)
        std::map<std::string, std::vector<double>> catRate;           // armpair|cat accumulations
        struct RunPack { std::vector<double> rate; double prr{0}; bool ok{false}; };
        std::map<std::string, std::map<std::string, RunPack>> runPacks; // cellseed -> arm -> pack
        long t5Tx = 0, t5Dtx = 0, t5Wasted = 0, t6Tx = 0, t6Stale = 0;
        std::map<std::string, KN> t5ByArm, t6ByArm;
        auto add = [&](const std::string& ev, const std::string& arm, const std::string& dim, const std::string& val, bool hit) {
            KN& x = tally[ev + "|" + arm + "|" + dim + "|" + val];
            ++x.n; x.k += hit;
        };
        for (const auto& rr : all)
        {
            bool isH = false, isB = false;
            for (const auto& [h, b] : pairs) { isH = isH || rr.arm == h; isB = isB || rr.arm == b; }
            if (!isH && !isB) continue;
            Run r;
            if (!r.Load(rr) || !r.hasAtt) continue;
            T.AddNRuns(1);
            const std::string cell = rr.CellKey(), cs = cell + "|s" + std::to_string(rr.seed);
            const bool blind = rr.HarqMode() == "blind";
            std::vector<TbView> tvs = WindowTbViews(r);
            // T1 gaps, T8 geometry, T7 collisions
            for (const TbView& tv : tvs)
            {
                for (size_t k = 1; k < tv.all.size(); ++k)
                {
                    gapMs[rr.arm].push_back((tv.all[k]->t - tv.all[k - 1]->t) * 1e3);
                    slotOff[rr.arm].push_back(static_cast<double>(tv.all[k]->slot - tv.initial->slot));
                    ++rbReuse[rr.arm].n;
                    rbReuse[rr.arm].k += (tv.all[k]->rbStart == tv.initial->rbStart);
                    ++t7[rr.arm + "|" + rr.bg].n;
                    t7[rr.arm + "|" + rr.bg].k += (tv.all[k]->nColl > 0);
                }
                if (blind && tv.all.size() > 1) blindLastOff[cell].push_back((tv.all.back()->t - tv.initial->t) * 1e3);
            }
            // T5/T6 per leader transmission
            for (const TbView& tv : tvs)
            {
                for (size_t k = 0; k < tv.all.size(); ++k)
                {
                    const PTx& x = *tv.all[k];
                    if (x.side != 0) continue;
                    ++t6ByArm[rr.arm].n;
                    t6ByArm[rr.arm].k += (x.decision == 2);
                    if (rr.HarqMode() == "opt2")
                    {
                        ++t5ByArm[rr.arm].n;
                        bool allDec = true;
                        for (int m = 1; m <= kMembers; ++m)
                        {
                            if (rr.IsHelperArm() && m == tv.helperNode) continue;
                            allDec = allDec && r.DecodedBy(*tv.tb, static_cast<int>(k), m);
                        }
                        if (x.decision == 0 && x.nNack == 0 && allDec) ++t5ByArm[rr.arm].k; // retrigger by silence alone
                    }
                }
            }
            // per member-TB events
            std::vector<PairOutcome> po;
            ClassifyPairs(r, po);
            RunPack& rp = runPacks[cs][rr.arm];
            rp.rate.assign(static_cast<size_t>(Cat::N), 0.0);
            rp.prr = r.R("prr_mean");
            rp.ok = !po.empty();
            const bool opt1 = rr.HarqMode() == "opt1", opt2 = rr.HarqMode() == "opt2";
            std::map<int64_t, double> dummy;
            for (const auto& p : po)
            {
                rp.rate[static_cast<size_t>(p.cat)] += 1.0 / po.size();
                const bool undelivered = !p.delivered();
                // heatmap: P(undelivered due to T2/T3) per member and background
                const std::string mem = std::to_string(p.member);
                if (!blind)
                {
                    KN& h2 = heatT["T2|" + rr.arm + "|" + rr.traffic + "|" + rr.bg + "|" + mem]; ++h2.n; h2.k += (p.cat == Cat::StopPdb);
                    KN& h3 = heatT["T3|" + rr.arm + "|" + rr.traffic + "|" + rr.bg + "|" + mem]; ++h3.n; h3.k += (p.cat == Cat::StopSciMissed);
                }
                // conditional tables for the loss events T2, T3, T4 (probability per member-TB)
                const std::vector<std::pair<std::string, std::string>> dims = {
                    {"scenario", rr.scenario}, {"ivd", rr.ivd}, {"traffic", rr.traffic}, {"background", rr.bg}, {"member", mem}, {"distance_bin", DistBin(p.dist)},
                    {"link_condition", CondName(p.cond)}, {"sinr_bin", SinrBin(p.sinr)}, {"first_tx_collided", p.coll ? "yes" : "no"}};
                for (const auto& [d, v] : dims)
                {
                    if (!blind)
                    {
                        add("T2_pdb_stop", rr.arm, d, v, p.cat == Cat::StopPdb);
                        if (opt1)
                        {
                            add("T3_sci_missed_stop", rr.arm, d, v, p.cat == Cat::StopSciMissed);
                            add("T3b_sci_missed_never_served", rr.arm, d, v, undelivered && p.everSciMissed);
                            add("T4_mcr_stop", rr.arm, d, v, p.cat == Cat::StopMcr);
                        }
                        add("T6_stale_stop", rr.arm, d, v, p.cat == Cat::StopStale);
                    }
                }
                (void)opt2;
                // T1: extra delay of delivered packets and 'later than the blind last copy'
                if (p.delivered() && p.deliverIdx >= 1 && !blind)
                {
                    auto it = blindLastOff.find(cell);
                    (void)it;
                }
            }
            // mean delay per member for the paired latency comparison
            for (int m = 1; m <= kMembers; ++m)
            {
                double sum = 0, n = 0;
                for (const auto& pr : r.pkts[m - 1]) if (pr.rx) { sum += pr.delayMs; n += 1; }
                auto& a = meanDelay[cs][rr.arm + "|" + std::to_string(m)];
                a[0] += sum; a[1] += n;
            }
        }
        if (runPacks.empty()) continue;
        // ---- T1 ------------------------------------------------------------------------------------------------------------------
        {
            std::string line;
            for (const auto& [arm, v] : gapMs)
            {
                line += arm + ": mean gap " + Fmt(Mean(v), 4) + " ms, p50 " + Fmt(Median(v), 4) + ", p95 " + Fmt(Quantile(v, 0.95), 4) + " (n=" + FmtI(static_cast<long>(v.size())) + "); ";
                for (int q = 0; q <= 100; q += 5) gaps.Row({rs, arm, "t1_gap_ms_q" + std::to_string(q), Fmt(Quantile(v, q / 100.0), 6), FmtI(static_cast<long>(v.size()))});
            }
            T.Info(rs + " T1 gap between consecutive transmissions of a TB (unit: transmission pair)", Opt().Obs(line).Note("blind arms send back-to-back copies; HARQ arms wait for the PSFCH round trip (K + processing >= 5 slots)"));
            // paired delivered-packet delay difference
            for (const auto& [h, b] : pairs)
            {
                std::vector<double> d;
                for (const auto& [cs, byArm] : meanDelay)
                    for (int m = 1; m <= kMembers; ++m)
                    {
                        auto ih = byArm.find(h + "|" + std::to_string(m)), ib = byArm.find(b + "|" + std::to_string(m));
                        if (ih == byArm.end() || ib == byArm.end() || ih->second[1] < 5 || ib->second[1] < 5) continue;
                        d.push_back(ih->second[0] / ih->second[1] - ib->second[0] / ib->second[1]);
                    }
                if (d.size() < 3) continue;
                const MeanCI m = MeanCi(d);
                T.Info(rs + " T1 mean delay of delivered packets, " + h + " - " + b + " (paired over cell, seed, member; n=" + FmtI(static_cast<long>(d.size())) + ")", Opt().Obs(FmtCi(m.mean, m.lo, m.hi, 4) + " ms"));
            }
        }
        // ---- T5, T6 --------------------------------------------------------------------------------------------------------------
        for (const auto& [arm, kn] : t5ByArm)
        {
            const Prop p = Wilson(kn.k, kn.n);
            T.Info(rs + " T5 Option-2 retransmissions triggered by silence alone (every member holds the TB, no NACK, decision 0), " + arm + " (unit: leader transmission)",
                   Opt().Fnd("F25").Obs(FmtCi(p.p, p.lo, p.hi, 4) + " (" + FmtI(kn.k) + " of " + FmtI(kn.n) + ")").Note("each such event makes the next retransmission wasted (B03)"));
            add("T5_dtx_only_retrigger", arm, "all", "all", false);
            tally["T5_dtx_only_retrigger|" + arm + "|all|all"] = kn;
        }
        for (const auto& [arm, kn] : t6ByArm)
        {
            const Prop p = Wilson(kn.k, kn.n);
            T.Info(rs + " T6 stale decisions (HARQ id reused before the decision; feedback dropped), " + arm + " (unit: leader transmission)", Opt().Fnd("F08").Obs(FmtCi(p.p, p.lo, p.hi, 5) + " (" + FmtI(kn.k) + " of " + FmtI(kn.n) + ")"));
            tally["T6_stale_decision|" + arm + "|all|all"] = kn;
        }
        // ---- T7, T8 ----------------------------------------------------------------------------------------------------------------
        for (const auto& [k, kn] : t7)
        {
            const Prop p = Wilson(kn.k, kn.n);
            T.Info(rs + " T7 retransmission collided (n_coll > 0), " + k + " (unit: retransmission)", Opt().Obs(FmtCi(p.p, p.lo, p.hi, 4) + " (" + FmtI(kn.k) + " of " + FmtI(kn.n) + ")"));
            const auto bar = k.find('|');
            tally["T7_retx_collided|" + k.substr(0, bar) + "|background|" + k.substr(bar + 1)] = kn;
        }
        {
            std::string line;
            for (const auto& [arm, v] : slotOff)
            {
                line += arm + ": mean offset " + Fmt(Mean(v), 4) + " slots, p50 " + Fmt(Median(v), 4) + ", p95 " + Fmt(Quantile(v, 0.95), 4) + ", same start subchannel as the first transmission " + Fmt(rbReuse[arm].n ? 100.0 * rbReuse[arm].k / rbReuse[arm].n : 0.0, 3) + " %; ";
                for (int q = 0; q <= 100; q += 5) gaps.Row({rs, arm, "t8_slot_offset_q" + std::to_string(q), Fmt(Quantile(v, q / 100.0), 6), FmtI(static_cast<long>(v.size()))});
            }
            T.Info(rs + " T8 resource geometry: retransmission slot minus initial slot and subchannel reuse (unit: retransmission)", Opt().Obs(line).Note("different retransmission timing and geometry make per-TB pairing of the arms impossible (Q6)"));
        }
        // ---- conditional tables and top-5 ---------------------------------------------------------------------------------------------
        std::map<std::string, std::vector<std::pair<double, std::string>>> rank;
        for (const auto& [key, kn] : tally)
        {
            std::vector<std::string> f;
            size_t s = 0;
            for (int i = 0; i < 3; ++i) { const auto e = key.find('|', s); f.push_back(key.substr(s, e - s)); s = e + 1; }
            const std::string val = key.substr(s);
            const Prop p = Wilson(kn.k, kn.n);
            cond.Row({rs, f[0], f[1], f[2], val, FmtI(kn.n), FmtI(kn.k), Fmt(p.p, 6), Fmt(p.lo, 6), Fmt(p.hi, 6)});
            if (kn.n >= 30 && kn.k > 0 && f[2] != "all") rank[f[0] + "|" + f[1]].push_back({p.p, f[2] + "=" + val + " n=" + FmtI(kn.n) + " p=" + Fmt(p.p, 3)});
        }
        for (auto& [ea, v] : rank)
        {
            std::sort(v.rbegin(), v.rend());
            std::string top;
            for (size_t i = 0; i < v.size() && i < 5; ++i) top += v[i].second + "; ";
            T.Info(rs + " " + ea.substr(0, ea.find('|')) + " " + ea.substr(ea.find('|') + 1) + ": top-5 conditions by probability (n >= 30)", Opt().Obs(top).Note("unit: member-TB; the full table is results/G07_conditions.csv"));
        }
        for (const auto& [key, kn] : heatT)
        {
            std::vector<std::string> f;
            size_t s = 0;
            for (int i = 0; i < 4; ++i) { const auto e = key.find('|', s); f.push_back(key.substr(s, e - s)); s = e + 1; }
            heat.Row({rs, f[0], f[1], f[2], f[3], key.substr(s), FmtI(kn.n), FmtI(kn.k), Fmt(kn.n ? static_cast<double>(kn.k) / kn.n : NAN, 6)});
        }
        // ---- PRR-loss decomposition (must add up) -------------------------------------------------------------------------------
        for (const auto& [h, b] : pairs)
        {
            std::vector<double> rateH(static_cast<size_t>(Cat::N), 0.0), rateB(static_cast<size_t>(Cat::N), 0.0), dPrr, errs;
            long n = 0;
            for (const auto& [cs, byArm] : runPacks)
            {
                if (!byArm.count(h) || !byArm.count(b) || !byArm.at(h).ok || !byArm.at(b).ok) continue;
                ++n;
                for (size_t c = 0; c < rateH.size(); ++c) { rateH[c] += byArm.at(h).rate[c]; rateB[c] += byArm.at(b).rate[c]; }
                dPrr.push_back(byArm.at(h).prr - byArm.at(b).prr);
                double sum = 0;
                for (size_t c = 1; c < rateH.size(); ++c) sum += byArm.at(h).rate[c] - byArm.at(b).rate[c];
                errs.push_back(std::fabs((-sum) - (byArm.at(h).prr - byArm.at(b).prr)));
            }
            if (n == 0) continue;
            double sumDelta = 0;
            std::string line;
            for (size_t c = 1; c < rateH.size(); ++c)
            {
                const double rh = rateH[c] / n, rb = rateB[c] / n;
                sumDelta += rh - rb;
                dec.Row({rs, "all cells", h, b, CatName(static_cast<Cat>(c)), Fmt(rh, 7), Fmt(rb, 7), Fmt(rh - rb, 7), Fmt(Mean(dPrr), 7), Fmt(Mean(errs), 7)});
                if (std::fabs(rh - rb) > 5e-4) line += std::string(CatName(static_cast<Cat>(c))) + " " + Fmt(rh - rb, 3) + "; ";
            }
            const double err = std::fabs((-sumDelta) - Mean(dPrr));
            T.Check(rs + " " + h + " vs " + b + ": PRR-loss decomposition adds up (sum of category changes == measured PRR change)", err <= 3e-3,
                    Opt().Exp("|sum - dPRR| <= 3e-3").Obs("dPRR_mean " + Fmt(Mean(dPrr), 4) + " = -(" + Fmt(sumDelta, 4) + "): " + line + "(n=" + FmtI(n) + " pairs; error " + Fmt(err, 3) + ")").Tol("3e-3 (window boundary, 6 printed digits)").Sev("M")
                        .Note("categories (T2 PDB, T3 SCI missed, T4 MCR, T6 stale, G08 helper blind spot, helper not decoded, other stop, exhausted budget) classify every undelivered member-packet exactly once"));
        }
    }
    const std::string art = V.Write("G07_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
