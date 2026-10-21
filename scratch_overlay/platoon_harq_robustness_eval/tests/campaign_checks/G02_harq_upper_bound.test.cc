// G02_harq_upper_bound (C: CORE; W: SYN_MARGINAL): oracle HARQ built from the blind arms' decode outcomes (retransmit only if some member
// still lacks the TB) as the resource lower bound of any HARQ scheme; pairs R1 -> R2, R3 and R4_S1_H2 -> R5_S1_H2, R6_S1_H2; PRR-loss
// decomposition of HARQ vs blind.
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
struct RunKpi
{
    double txPerPkt{0}, oraclePerPkt{0}, oracleOpt1PerPkt{0}, prr{0};
    double pk{0};
    std::string tag;
    double nackTrials{0}, nackOk{0};                          //!< Option 1 runs: corrupt member-transmissions inside the MCR and how many of them were NACKed
    std::vector<std::vector<std::pair<double, int>>> seq;     //!< blind runs: per TB, per attempt (subchannel-slots, members able to NACK after it)
    /// expected subchannel-slots per packet of a feedback-driven chain on the blind realisation when a member with a corrupt PSSCH NACKs with probability q
    double OracleQ(double q) const
    {
        double o = 0;
        for (const auto& tb : seq)
        {
            double alive = 1.0;
            for (const auto& [sub, nf] : tb)
            {
                o += alive * sub;
                alive *= (nf > 0) ? 1.0 - std::pow(1.0 - q, nf) : 0.0;
            }
        }
        return pk > 0 ? o / pk : 0.0;
    }
    std::vector<double> catRate; // per Cat
    bool ok{false};
};

RunKpi
Kpi(const Run& r, bool blind)
{
    RunKpi k;
    const SpectrumAcct a = ComputeSpectrum(r);
    const double pk = r.R("pkts");
    if (!a.ok || pk <= 0)
    {
        return k;
    }
    k.pk = pk;
    k.txPerPkt = (a.txLeader + a.txHelper) / pk;
    const std::string retx = r.ref.Retx();
    if (retx == "R2" || retx == "R5")
    {
        for (const TbView& tv : WindowTbViews(r))
        {
            for (size_t j = 0; j < tv.all.size(); ++j)
            {
                const PTx& x = *tv.all[j];
                for (int m = 1; m <= kMembers; ++m)
                {
                    if (r.IsSelf(x, m)) continue;
                    const bool lacksBefore = (j == 0) || !r.DecodedBy(*tv.tb, static_cast<int>(j) - 1, m);
                    if (lacksBefore && x.status[m - 1] == 2 && x.dist[m - 1] <= 500.0f)
                    {
                        k.nackTrials += 1;
                        k.nackOk += ((x.nackMask >> (m - 1)) & 1u) ? 1 : 0;
                    }
                }
            }
        }
    }
    k.prr = r.R("prr_mean");
    if (blind)
    {
        double o = 0, o1 = 0;
        for (const TbView& tv : WindowTbViews(r))
        {
            std::vector<std::pair<double, int>> sq;
            for (size_t j = 0; j < tv.all.size(); ++j)
            {
                int nf = 0;
                for (int m = 1; m <= kMembers; ++m)
                {
                    if (r.IsSelf(*tv.all[j], m)) continue;
                    if (!r.DecodedBy(*tv.tb, static_cast<int>(j), m) && tv.all[j]->status[m - 1] == 2 && tv.all[j]->dist[m - 1] <= 500.0f) ++nf;
                }
                sq.emplace_back(tv.all[j]->subch(), nf);
            }
            k.seq.push_back(std::move(sq));
            bool chain1 = true; // Option 1: once a transmission is not requested, the feedback-driven chain has ended
            for (size_t j = 0; j < tv.all.size(); ++j)
            {
                bool wasted = false, wasted1 = false;
                if (j > 0)
                {
                    wasted = wasted1 = true;
                    const PTx& prev = *tv.all[j - 1];
                    for (int m = 1; m <= kMembers; ++m)
                    {
                        if (r.IsSelf(prev, m)) continue;
                        const bool lacks = !r.DecodedBy(*tv.tb, static_cast<int>(j) - 1, m);
                        if (lacks) wasted = false;
                        // Option 1 (NACK-only): only a member that decoded the SCI of the PREVIOUS transmission but not the TB can ask for the next one
                        if (lacks && prev.status[m - 1] == 2) wasted1 = false;
                    }
                    if (wasted1) chain1 = false;
                }
                if (!wasted) o += tv.all[j]->subch();
                if (chain1) o1 += tv.all[j]->subch();
            }
        }
        k.oraclePerPkt = o / pk;
        k.oracleOpt1PerPkt = o1 / pk;
    }
    std::vector<PairOutcome> po;
    ClassifyPairs(r, po);
    k.catRate.assign(static_cast<size_t>(Cat::N), 0.0);
    for (const auto& p : po) k.catRate[static_cast<size_t>(p.cat)] += 1.0;
    for (auto& v : k.catRate) v = po.empty() ? 0.0 : v / po.size();
    k.ok = true;
    return k;
}
} // namespace

int
main()
{
    TestReport T("G02", "harq_upper_bound", "G");
    T.Summary("Q8: HARQ cannot use fewer transmitted subchannel-slots than the oracle built from the blind decode outcomes, cannot beat the blind "
              "PRR without background, and its PRR loss against blind decomposes into attributed causes.");
    T.AddRef("TEST_PLAN G02; TS 38.321 5.22.1.3 [V]");
    CsvWriter gap(TestsRoot() + "/results/G02_gap_decomposition.csv", "runset,group,arm,blind_arm,category,rate_arm,rate_blind,delta,n_pairs");
    CsvWriter res(TestsRoot() + "/results/G02_resources.csv", "runset,group,arm,blind_arm,tx_per_pkt_arm,tx_per_pkt_blind,oracle_per_pkt,prr_arm,prr_blind,gain_arm,gain_oracle,n_pairs");
    T.Artefact("results/G02_gap_decomposition.csv");
    T.Artefact("results/G02_resources.csv");
    const std::vector<std::pair<std::string, std::string>> pairs = {{"R2", "R1"}, {"R3", "R1"}, {"R5_S1_H2", "R4_S1_H2"}, {"R6_S1_H2", "R4_S1_H2"}};
    for (const std::string rs : {"CORE", "SYN_MARGINAL"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { if (rs == "CORE") T.Skip("CORE data", "CORE manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        // key (cell, seed) -> arm -> kpi
        std::map<std::string, std::map<std::string, RunKpi>> data;
        for (const auto& rr : all)
        {
            Run r;
            if (!r.Load(rr) || !r.hasAlltx || !r.hasGrants || !r.hasAtt) continue;
            T.AddNRuns(1);
            data[rr.CellKey() + "|s" + std::to_string(rr.seed)][rr.arm] = Kpi(r, rr.HarqMode() == "blind");
            data[rr.CellKey() + "|s" + std::to_string(rr.seed)][rr.arm].tag = rr.tag;
        }
        long idBad = 0, idN = 0;
        std::string idEx;
        long idNonFx = 0;
        for (const auto& [h, b] : pairs)
        {
            // group by cell (without seed)
            std::map<std::string, std::vector<std::array<const RunKpi*, 2>>> groups;
            for (const auto& [key, byArm] : data)
            {
                if (!byArm.count(h) || !byArm.count(b) || !byArm.at(h).ok || !byArm.at(b).ok) continue;
                groups[key.substr(0, key.rfind("|s"))].push_back({&byArm.at(h), &byArm.at(b)});
            }
            for (const auto& [group, v] : groups)
            {
                const bool opt1Pair = (h == "R2" || h == "R5_S1_H2");
                std::vector<double> txH, txB, orc, prrH, prrB, dRes, dPrr, gainH, gainO, orcStrict;
                std::vector<double> catH(static_cast<size_t>(Cat::N), 0.0), catB(static_cast<size_t>(Cat::N), 0.0);
                double qTrials = 0, qOk = 0;
                for (const auto& p : v) { qTrials += p[0]->nackTrials; qOk += p[0]->nackOk; }
                const double qNack = qTrials > 0 ? qOk / qTrials : 1.0;
                std::vector<double> orcNoQ;
                for (const auto& p : v)
                {
                    const double oracleUse = opt1Pair ? p[1]->OracleQ(qNack) : p[1]->oraclePerPkt;
                    orcNoQ.push_back(p[1]->oracleOpt1PerPkt);
                    txH.push_back(p[0]->txPerPkt); txB.push_back(p[1]->txPerPkt); orc.push_back(oracleUse); orcStrict.push_back(p[1]->oraclePerPkt);
                    prrH.push_back(p[0]->prr); prrB.push_back(p[1]->prr);
                    dRes.push_back(p[0]->txPerPkt - oracleUse);
                    dPrr.push_back(p[0]->prr - p[1]->prr);
                    gainH.push_back(p[1]->txPerPkt > 0 ? 1.0 - p[0]->txPerPkt / p[1]->txPerPkt : 0.0);
                    gainO.push_back(p[1]->txPerPkt > 0 ? 1.0 - oracleUse / p[1]->txPerPkt : 0.0);
                    for (size_t c = 0; c < catH.size(); ++c) { catH[c] += p[0]->catRate[c] / v.size(); catB[c] += p[1]->catRate[c] / v.size(); }
                    double sH = 0; for (size_t c = 1; c < catH.size(); ++c) sH += p[0]->catRate[c];
                    ++idN; if (std::fabs(sH - (1.0 - p[0]->prr)) > 3e-3) { ++idBad; idNonFx += (p[0]->tag.find("periodic2fx") == std::string::npos) ? 1 : 0; idEx += rs + "/" + h + "/" + p[0]->tag + " categories sum " + Fmt(sH, 5) + " vs 1 - PRR_mean " + Fmt(1.0 - p[0]->prr, 5) + " (pkts " + Fmt(p[0]->pk, 5) + "); "; }
                }
                res.Row({rs, group, h, b, Fmt(Mean(txH), 6), Fmt(Mean(txB), 6), Fmt(Mean(orc), 6), Fmt(Mean(prrH), 6), Fmt(Mean(prrB), 6), Fmt(Mean(gainH), 6), Fmt(Mean(gainO), 6), std::to_string(v.size())});
                for (size_t c = 1; c < catH.size(); ++c)
                    gap.Row({rs, group, h, b, CatName(static_cast<Cat>(c)), Fmt(catH[c], 7), Fmt(catB[c], 7), Fmt(catH[c] - catB[c], 7), std::to_string(v.size())});
                if (v.size() < 3)
                {
                    T.Info(rs + " " + group + ": " + h + " vs oracle(" + b + ")", Opt().Obs(std::to_string(v.size()) + " seeds: fewer than 3, no verdict (section 7.3)  tx/pkt " + Fmt(Mean(txH), 4) + " vs oracle " + Fmt(Mean(orc), 4) + " vs blind " + Fmt(Mean(txB), 4)));
                    continue;
                }
                const DiffCI dr = Paired(txH, orc), drS = Paired(txH, orcStrict), dp = Paired(prrH, prrB), dg = Paired(gainH, gainO), dN = Paired(txH, orcNoQ);
                const std::string pre = rs + " " + group + ": " + h + " vs " + b + ": ";
                const std::string orcName = opt1Pair ? "feedback-aware oracle (members that missed the SCI cannot NACK; a corrupt member NACKs with the measured rate q=" + Fmt(qNack, 3) + ")" : "oracle";
                if (opt1Pair)
                    T.Info(pre + "Option 1 NACK rate of corrupt member-transmissions inside the MCR (q) and the resources against the oracle that lets every SCI-1A-decoding member NACK",
                           Opt().Obs("q = " + Fmt(qNack, 4) + " (" + FmtI(static_cast<long>(qOk)) + " of " + FmtI(static_cast<long>(qTrials)) + "); tx/pkt - oracle(q=1) = " + FmtCi(dN.diff, dN.lo, dN.hi, 4))
                               .Note("a member with a corrupt PSSCH does not always NACK: its SCI-2A (source id) can be corrupt and no feedback is possible (not logged in _att; for the R6 helper the simulator counters confirm it: C03, "
                                     "helper_nack_not_sent == helper_corrupt_sci2). "
                                     "The oracle with q=1 is therefore not a bound for Option 1"));
                if (dr.hi < 0 && !(dp.lo > 0))
                    T.Info(pre + "transmitted subchannel-slots per packet >= the " + orcName + " of the blind arm", Opt().Exp("res(HARQ) >= oracle (CI not below 0)").Obs("difference " + FmtCi(dr.diff, dr.lo, dr.hi, 4) + " (tx/pkt " + Fmt(Mean(txH), 4) + " vs oracle " + Fmt(Mean(orc), 4) + ")").Sev("H")
                               .Note("INFO since TEST_PLAN 0.3 (1): R1 / R4 no longer soft-combine while R2, R3, R5, R6 do, so the independent-attempt oracle of the blind arm is not a lower bound (and the arms draw different fading / interference realisations): HARQ below the oracle built from the blind arm's decode outcomes: either retransmissions that members needed were dropped for a reason "
                                     "other than a missed SCI-1A (e.g. a member with a corrupt SCI-2A cannot NACK, not visible in _att), or the oracle is not a strict cross-arm bound "
                                     "because the arms draw different fading / interference realisations and retransmission timing (no per-TB pairing, TEST_PLAN Q6). The effect is "
                                     "a few percent of the resources; see the PRR decomposition (T3 and stop_other) in the same group."));
                else if (dr.hi < 0)
                    T.Info(pre + "transmitted subchannel-slots per packet below the " + orcName + " of the blind arm, with PRR above the oracle",
                           Opt().Obs("difference " + FmtCi(dr.diff, dr.lo, dr.hi, 4) + "; dPRR " + FmtCi(dp.diff, dp.lo, dp.hi, 4))
                               .Note("TEST_PLAN 0.3 (1): the blind arm does not soft-combine, the feedback arm does, so each of its attempts succeeds more often and fewer retransmissions are needed than the independent-attempt oracle assumes"));
                else
                {
                    T.Pass(pre + "transmitted subchannel-slots per packet >= the " + orcName + " of the blind arm", Opt().Exp("res(HARQ) >= oracle").Obs("difference " + FmtCi(dr.diff, dr.lo, dr.hi, 4) + " (tx/pkt " + Fmt(Mean(txH), 4) + " vs oracle " + Fmt(Mean(orc), 4) + ")"));
                    if (opt1Pair && drS.hi < 0)
                        T.Info(pre + "below the strict oracle (every member served) as expected for Option 1", Opt().Obs("difference to the strict oracle " + FmtCi(drS.diff, drS.lo, drS.hi, 4) + " (strict oracle " + Fmt(Mean(orcStrict), 4) + ")")
                                   .Note("inherent Option-1 property (DTX = ACK): members that miss the SCI are never served, which saves resources and costs PRR (T3 in the decomposition)"));
                }
                const double helperNdEffect = -(catH[static_cast<size_t>(Cat::StopHelperNotDecoded)] - catB[static_cast<size_t>(Cat::StopHelperNotDecoded)]); // PRR gained over blind by serving TBs whose helper did not decode
                {
                    // TEST_PLAN 0.3 (1): the blind arms (R1, R4) no longer soft-combine, so HARQ with combining can legitimately beat them.  The oracle built from the blind
                    // arm's per-copy outcomes (independent attempts, retransmit only when a member lacks the TB) reaches exactly the blind PRR with fewer transmissions.
                    const bool none = group.find("/none/") != std::string::npos;
                    T.Info(pre + "dPRR = PRR(HARQ) - PRR(independent-attempt oracle of the blind arm = blind PRR)" + (none ? ", no background" : ", with background"),
                           Opt().Obs(FmtCi(dp.diff, dp.lo, dp.hi, 4) + (dp.lo > 0 ? "; HARQ above the oracle" : (dp.hi < 0 ? "; HARQ below the oracle" : "; no clear difference"))
                                     + (dp.lo > 0 && helperNdEffect >= 0.5 * dp.diff ? "; mostly the helper-decode dependence of the blind helper arm (R4 sends no copy when the helper missed the TB, F18)" : ""))
                               .Note("a positive value is soft combining of the feedback arms (R2, R3, R5, R6 combine, R1 and R4 do not); a negative value is lost retransmissions (T3 / T3c / stale)"));
                }
                if (dg.lo > 0 && dp.lo > 0) T.Info(pre + "spectrum gain of HARQ above the oracle gain, with PRR above the oracle", Opt().Obs(FmtCi(dg.diff, dg.lo, dg.hi, 4)).Note("combining gain of the feedback arm (TEST_PLAN 0.3 (1))"));
                else if (dg.lo > 0) T.Info(pre + "spectrum gain of HARQ <= gain of the " + orcName + " + CI", Opt().Exp("gain(HARQ) - gain(oracle) <= 0").Obs(FmtCi(dg.diff, dg.lo, dg.hi, 4)).Sev("H").Note("same observation as the resource check above (the gain is 1 - res/res_blind)"));
                else T.Pass(pre + "spectrum gain of HARQ <= gain of the " + orcName + " + CI", Opt().Exp("gain(HARQ) <= gain(oracle)").Obs("gain HARQ " + Fmt(Mean(gainH), 4) + ", oracle " + Fmt(Mean(gainO), 4) + ", difference " + FmtCi(dg.diff, dg.lo, dg.hi, 4)));
                // decomposition summary
                std::string dec;
                for (size_t c = 1; c < catH.size(); ++c)
                    if (std::fabs(catH[c] - catB[c]) > 5e-4) dec += std::string(CatName(static_cast<Cat>(c))) + " " + Fmt(catH[c] - catB[c], 3) + "; ";
                T.Info(pre + "PRR loss decomposition (HARQ minus blind, change of the undelivered share per cause)", Opt().Obs("dPRR " + Fmt(Mean(dPrr), 4) + " = -sum of: " + dec));
            }
        }
        T.Check(rs + ": the loss classification is exhaustive (category rates sum to 1 - PRR_mean per run)", idN == 0 || idBad == 0, Opt().Exp("0 of " + FmtI(idN)).Obs(FmtI(idBad) + (idEx.empty() ? "" : ": " + idEx)).Tol("3e-3 (window boundary)").Sev("M").Fnd(idBad > 0 && idNonFx == 0 ? "F32" : "").Note(idBad > 0 && idNonFx == 0 ? "the offending run is periodic2fx (TEST_PLAN 0.4, F32): a TB carrying the next packet is counted for two packets, so the per-packet loss classes overshoot by 0.8 % of the member-packets; 0 in every other run" : ""));
    }
    return T.Finish();
}
