// B03_option2_workflow (W: SYN_NEAR, SYN_MARGINAL, SYN_DTX; R3, R6_S1_H2) and (M) stock-MAC characterisation (DV2/F12):
// HARQ Option 2 (groupcast ACK/NACK, cast type 01): ACK iff decoded (also a TB already decoded on an earlier copy, F25),
// NACK otherwise, DTX = NACK; the transmitter declares success only if every expected member ACKed.
#include "common/csv.h"
#include "common/harness.h"
#include "common/micro_sl.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <algorithm>
#include <cmath>
#include <map>

using namespace ptest;

namespace
{
struct F25Stat
{
    long runs{0}, tbs{0}, opps{0}, succ{0}, wasted{0}, a{0}, b{0}, wastedTx{0}, totalTx{0}, tbsWasted{0};
    double wastedSub{0}, totalSub{0}, expB{0};
};

/// Option-2 arms: transmissions sent although every member already held the TB, and the classification of the leader's DTX-only retriggers
F25Stat
F25Analyse(const std::vector<RunRef>& runs, const std::string& arm, bool /*core*/)
{
    F25Stat S;
    struct Ev
    {
        std::vector<int> A;
        bool b;
    };
    std::vector<Ev> evs;
    std::map<int, long> first0, firstN;
    for (const auto& rr : runs)
    {
        Run r;
        if (!r.Load(rr) || !r.hasAtt)
        {
            continue;
        }
        ++S.runs;
        for (const TbView& tv : WindowTbViews(r))
        {
            ++S.tbs;
            bool anyW = false;
            for (int m = 1; m <= kMembers; ++m)
            {
                if (!r.IsSelf(*tv.all[0], m))
                {
                    ++firstN[m];
                    first0[m] += (tv.all[0]->status[m - 1] == 0) ? 1 : 0;
                }
            }
            for (size_t j = 0; j < tv.all.size(); ++j)
            {
                const PTx& x = *tv.all[j];
                ++S.totalTx;
                S.totalSub += x.subch();
                if (j == 0)
                {
                    continue;
                }
                bool allBefore = true;
                for (int m = 1; m <= kMembers; ++m)
                {
                    if (!r.IsSelf(x, m))
                    {
                        allBefore = allBefore && r.DecodedBy(*tv.tb, static_cast<int>(j) - 1, m);
                    }
                }
                if (allBefore)
                {
                    ++S.wastedTx;
                    S.wastedSub += x.subch();
                    anyW = true;
                }
                // the leader's decision on this retransmission: every (non-helper) member holds the TB after it, nobody NACKed
                if (x.side != 0 || x.decision == 2 || x.nNack != 0)
                {
                    continue;
                }
                bool allHold = true;
                std::vector<int> A;
                bool has2 = false;
                for (int m = 1; m <= kMembers; ++m)
                {
                    if (arm == "R6_S1_H2" && m == tv.helperNode)
                    {
                        continue;
                    }
                    if (!r.DecodedBy(*tv.tb, static_cast<int>(j), m))
                    {
                        allHold = false;
                        break;
                    }
                    if (r.DecodedBy(*tv.tb, static_cast<int>(j) - 1, m))
                    {
                        A.push_back(m);
                        has2 = has2 || x.status[m - 1] == 2;
                    }
                }
                if (!allHold || A.empty())
                {
                    continue;
                }
                ++S.opps;
                if (x.decision == 1)
                {
                    ++S.succ;
                }
                else if (x.decision == 0)
                {
                    ++S.wasted;
                    if (has2) ++S.a;
                    else ++S.b;
                }
                if (!has2)
                {
                    evs.push_back({A, x.decision == 0});
                }
            }
            S.tbsWasted += anyW ? 1 : 0;
        }
    }
    for (const auto& e : evs)
    {
        double keep = 1.0;
        for (int m : e.A)
        {
            const double p0 = firstN[m] > 0 ? static_cast<double>(first0[m]) / firstN[m] : 0.0;
            keep *= (1.0 - p0);
        }
        S.expB += 1.0 - keep;
    }
    return S;
}
} // namespace

int
main()
{
    TestReport T("B03", "option2_workflow", "B");
    T.Summary("Option 2: success only if every member ACKed (DTX = NACK); no false ACK; Option-2 receivers must re-ACK a TB "
              "they already decoded (F25: they do not, so retransmissions are wasted); the stock MAC cancels on the first ACK "
              "(DV2) and the study bypasses it.");
    T.AddRef("TS 38.213 16.3, TS 38.321 5.22.1.3 (Option 2: ACK only if every expected member ACKed; DTX = NACK) [V]");
    T.AddRef("TEST_PLAN DV2 / F12 / F25");
    ViolationLog V;

    // ---- SYN_NEAR ------------------------------------------------------------------------------------------------------
    for (const std::string arm : {"R3", "R6_S1_H2"})
    {
        T.AddRunset("SYN_NEAR");
        long runs = 0, tbs = 0, lossless = 0, losslessOk = 0, relaysLossless = 0, allAckRun = 0, allAckExp = 0;
        for (const auto& rr : Select(LoadManifest("SYN_NEAR"), arm))
        {
            Run r;
            if (!r.Load(rr))
            {
                continue;
            }
            ++runs;
            T.AddNRuns(1);
            for (const PTx& x : r.txs)
            {
                allAckExp += (x.side == 0 && x.decision == 1) ? 1 : 0;
            }
            allAckRun += static_cast<long>(r.R("stale_decisions", 0) * 0); // placeholder keeps the column read explicit
            for (const TbView& tv : WindowTbViews(r))
            {
                ++tbs;
                bool all = true;
                for (int m = 1; m <= kMembers; ++m) all = all && tv.initial->status[m - 1] == 1;
                if (all)
                {
                    ++lossless;
                    losslessOk += (tv.leader.size() == 1 && tv.initial->decision == 1) ? 1 : 0;
                    relaysLossless += static_cast<long>(tv.helper.size());
                }
            }
        }
        (void)allAckRun;
        if (runs == 0)
        {
            T.Skip("SYN_NEAR/" + arm, "no runs");
            continue;
        }
        T.Check("SYN_NEAR " + arm + ": every TB whose first transmission every member decoded has decision 1 and one transmission",
                lossless > 0 && losslessOk == lossless,
                Opt().Exp(FmtI(lossless) + " lossless TBs conform").Obs(FmtI(losslessOk) + " conform; " + FmtI(tbs - lossless) +
                                                                       " TBs had a member that did not decode the first transmission (half-duplex / SCI miss, excluded)")
                    .Tol("0").Sev("H").Ref("TS 38.321 5.22.1.3 [V]"));
        if (arm == "R6_S1_H2")
        {
            T.Check("SYN_NEAR R6: no relay for TBs that every other member ACKed", relaysLossless == 0,
                    Opt().Exp("0 copies for " + FmtI(lossless) + " lossless TBs").Obs(FmtI(relaysLossless)).Tol("0").Sev("H"));
        }
        T.Info("SYN_NEAR " + arm + ": all-ACK decisions (whole run)", Opt().Obs(FmtI(allAckExp)));
    }

    // ---- SYN_MARGINAL: per transmission invariants -------------------------------------------------------------------------
    for (const std::string arm : {"R3", "R6_S1_H2"})
    {
        T.AddRunset("SYN_MARGINAL");
        long runs = 0, txs = 0, falseAck = 0, cut = 0, cutN = 0;
        long dtxOnly = 0, dtxOnlyWasted = 0, redundantCtx = 0, redundantAck = 0, wastedSubch = 0;
        long nackNoDecision = 0;
        long faMembers = 0, faNoNack = 0, faPrevDec = 0; // false ACKs: members lacking the TB, those that never NACKed (SCI-2A failed), those whose previous TB on the HARQ id they had decoded
        for (const auto& rr : Select(LoadManifest("SYN_MARGINAL"), arm))
        {
            Run r;
            if (!r.Load(rr) || !r.hasAtt)
            {
                continue;
            }
            ++runs;
            T.AddNRuns(1);
            for (const TbView& tv : WindowTbViews(r))
            {
                const std::vector<const PTx*>& L = tv.leader; // R3: all; R6: leader transmissions only
                for (size_t k = 0; k < L.size(); ++k)
                {
                    const PTx& x = *L[k];
                    ++txs;
                    // cumulative decode over every transmission (leader and helper copies) up to this leader tx
                    int posAll = -1;
                    for (size_t a = 0; a < tv.all.size(); ++a)
                    {
                        if (tv.all[a] == &x)
                        {
                            posAll = static_cast<int>(a);
                        }
                    }
                    bool allDec = true, allDecBefore = true, anyDecBefore = false;
                    for (int m = 1; m <= kMembers; ++m)
                    {
                        if (arm == "R6_S1_H2" && m == tv.helperNode)
                        {
                            continue; // the named helper never ACKs: not part of the success rule
                        }
                        allDec = allDec && r.DecodedBy(*tv.tb, posAll, m);
                        const bool before = posAll > 0 && r.DecodedBy(*tv.tb, posAll - 1, m);
                        allDecBefore = allDecBefore && before;
                        anyDecBefore = anyDecBefore || before;
                    }
                    if (x.decision == 1 && !allDec)
                    {
                        ++falseAck;
                        // classify the members that lack the TB: stale prev-decoded flag (SCI-2A of the new TB failed, previous TB on the HARQ id decoded)?
                        for (int m = 1; m <= kMembers; ++m)
                        {
                            if ((arm == "R6_S1_H2" && m == tv.helperNode) || r.DecodedBy(*tv.tb, posAll, m))
                            {
                                continue;
                            }
                            ++faMembers;
                            bool nacked = false;
                            for (int q = 0; q < posAll; ++q) nacked = nacked || ((tv.all[q]->nackMask >> (m - 1)) & 1u);
                            faNoNack += nacked ? 0 : 1;
                            // previous TB with the same HARQ id
                            const Tb* prevTb = nullptr;
                            double pt = -1;
                            for (const auto& [id2, tb2] : r.tbs)
                            {
                                if (tb2.harq == tv.tb->harq && id2 != tv.tb->id && !tb2.tx.empty() && r.txs[tb2.tx.front()].t < tv.initial->t && r.txs[tb2.tx.front()].t > pt)
                                {
                                    pt = r.txs[tb2.tx.front()].t;
                                    prevTb = &tb2;
                                }
                            }
                            if (prevTb && !prevTb->tx.empty() && r.DecodedBy(*prevTb, static_cast<int>(prevTb->tx.size()) - 1, m)) ++faPrevDec;
                        }
                        V.Add(arm + " false ACK (decision 1 while a member has not decoded)", rr.tag + " tx slot " + FmtI(x.slot));
                    }
                    // a decision 0 transmission below the budget must be followed by the next transmission (no cut short)
                    const bool last = static_cast<int>(k) + 1 >= (arm == "R3" ? r.maxNumTx : 1 + 0);
                    if (arm == "R3" && x.decision == 0 && !last)
                    {
                        ++cutN;
                        if (k + 1 >= L.size())
                        {
                            ++cut;
                            V.Add("R3 TB ended although decision 0 and transmissions remain", rr.tag + " tx slot " + FmtI(x.slot));
                        }
                    }
                    // F25: every member decoded (cumulatively) but the leader still saw no all-ACK -> DTX-only retrigger
                    if (x.decision != 2 && allDec && x.nNack == 0 && k >= 1 && anyDecBefore)
                    {
                        ++redundantCtx;
                        redundantAck += (x.decision == 1);
                    }
                    if (x.decision == 0 && allDec && x.nNack == 0)
                    {
                        ++dtxOnly;
                        const bool nextExists = (k + 1 < L.size()) || (arm == "R6_S1_H2" && !tv.helper.empty());
                        dtxOnlyWasted += nextExists ? 1 : 0;
                        // the wasted transmission is the next one of the TB, whoever sends it (a helper copy in R6)
                        if (posAll >= 0 && static_cast<size_t>(posAll) + 1 < tv.all.size())
                        {
                            wastedSubch += static_cast<long>(tv.all[posAll + 1]->rbLen / kSubchPrbs);
                        }
                    }
                    if (x.decision < 0)
                    {
                        ++nackNoDecision;
                    }
                }
            }
        }
        if (runs == 0)
        {
            T.Skip("SYN_MARGINAL/" + arm, "no runs");
            continue;
        }
        const std::string pre = "SYN_MARGINAL " + arm + ": ";
        T.Check(pre + "no false ACK: decision 1 only when every (non-helper) member has decoded", falseAck == 0,
                Opt().Exp("0 of " + FmtI(txs) + " transmissions")
                    .Obs(FmtI(falseAck) + " false ACKs (" + FmtI(faMembers) + " member-TB pairs lacked the TB: " + FmtI(faNoNack) + " never sent a NACK = SCI-2A failed, " + FmtI(faPrevDec) + " had decoded the previous TB on the same HARQ id)")
                    .Tol("0").Sev("H").Fnd("F31").Ref("TS 38.321 5.22.1.3 [V]")
                    .Note("F31 (new): the F25 fix ACKs every retransmission of a TB the receiver thinks it holds (nr-spectrum-phy.cc IsPrevDecoded, keyed by (txRnti, HARQ id)).  RemovePrevDecoded runs only after SCI-2A of an ndi=1 transmission decoded OK, so a receiver whose SCI-2A of the new TB failed (status 2, no NACK) keeps the flag of the PREVIOUS TB on the same HARQ id and ACKs the retransmission although it never decoded the TB: the leader declares success (decision 1) with a member lacking the TB.  The same stale flag existed before the fix: such a member dropped every retransmission silently (v1: 0 of 254 R2 and 0 of 256 R3 member-TB pairs with SCI-2 failure at the initial and the previous TB decoded ever decoded the TB later); the fix turns the silent drop into a false ACK"));
        if (arm == "R3")
        {
            T.Check(pre + "bypass of the stock first-ACK-cancels rule: no TB ends with decision 0 while transmissions remain", cut == 0,
                    Opt().Exp("0 of " + FmtI(cutN)).Obs(FmtI(cut)).Tol("0").Sev("H").Fnd("F12").Ref("DV2: stock MAC cancels on the first ACK (nr-sl-ue-mac-harq.cc)"));
        }
        // F25 raw counts (the verdict is the classification below)
        T.Info(pre + "F25 raw: transmissions where every member had decoded, no NACK, yet decision 0 (leader read DTX as NACK)",
               Opt().Fnd("F25").Obs(FmtI(dtxOnly) + " transmissions; " + FmtI(redundantAck) + " of " + FmtI(redundantCtx) + " retransmissions with already-decoded members reached decision 1; "
                                    + FmtI(dtxOnlyWasted) + " were followed by another transmission, " + FmtI(wastedSubch) + " subchannel-slots"));
        T.Metric(arm + "_f25_dtx_only_triggers", static_cast<double>(dtxOnly));
        T.Metric(arm + "_f25_wasted_subchannel_slots", static_cast<double>(wastedSubch));
        T.Metric(arm + "_f25_redundant_ctx", static_cast<double>(redundantCtx));
        T.Metric(arm + "_f25_redundant_ack", static_cast<double>(redundantAck));
        (void)nackNoDecision;
    }

    // ---- F25 after the fix (TEST_PLAN 0.2): classification of the residual DTX-only retriggers, SYN_MARGINAL and CORE -------------------------
    CsvWriter f25csv(TestsRoot() + "/results/B03_f25_before_after.csv", "runset,arm,version,runs,tbs,total_tx,wasted_tx,total_subch,wasted_subch,tbs_wasted,opportunities,decided_success,decided_nack,class_a_status2,class_b_status0,expected_b");
    T.Artefact("results/B03_f25_before_after.csv");
    auto f25row = [&](const std::string& rs, const std::string& arm, const std::string& ver, const F25Stat& f) {
        f25csv.Row({rs, arm, ver, FmtI(f.runs), FmtI(f.tbs), FmtI(f.totalTx), FmtI(f.wastedTx), Fmt(f.totalSub, 8), Fmt(f.wastedSub, 8), FmtI(f.tbsWasted), FmtI(f.opps), FmtI(f.succ), FmtI(f.wasted), FmtI(f.a), FmtI(f.b), Fmt(f.expB, 6)});
    };
    for (const std::string rs : {"SYN_MARGINAL", "CORE"})
    {
        const auto now = LoadManifest(rs);
        const auto v1 = LoadManifest(rs, OutDirV1());
        if (now.empty())
        {
            if (rs == "CORE") T.Skip("F25 residual classification (CORE)", "CORE manifest not present (campaign stage not run yet)");
            continue;
        }
        T.AddRunset(rs);
        for (const std::string arm : {"R3", "R6_S1_H2"})
        {
            const F25Stat fx = F25Analyse(Select(now, arm), arm, rs == "CORE");
            if (fx.runs == 0) continue;
            f25row(rs, arm, "after", fx);
            T.AddNRuns(fx.runs);
            const std::string pre = rs + " " + arm + ": ";
            // plausibility bound for class (b): P(some already-decoded member misses the SCI-1) from the members' first-transmission status-0 rate
            const double bound = fx.expB + 1.645 * std::sqrt(std::max(fx.expB, 0.0)) + 1.0;
            const bool ok = static_cast<double>(fx.b) <= bound;
            T.Check(pre + "F25 (fixed): every DTX-only retrigger is explained: (a) an already-decoded member with SCI-2 failed (status 2, cannot ACK) or (b) only status 0 members, not above the SCI-1 miss plausibility bound", ok,
                    Opt().Fnd("F25").Sev("M").Exp("(b) <= " + Fmt(bound, 3))
                        .Obs(FmtI(fx.opps) + " retransmissions with all members holding the TB and no NACK: " + FmtI(fx.succ) + " decided success (re-ACKs received); " + FmtI(fx.wasted) + " decided NACK = DTX: (a) status 2 "
                             + FmtI(fx.a) + ", (b) only status 0 " + FmtI(fx.b) + " (expected (b) from first-transmission SCI-miss rates: " + Fmt(fx.expB, 3) + ")")
                        .Tol("(b) <= expected + 1.645 sqrt(expected) + 1")
                        .Note("TS 38.321 5.22.2.2: an Option-2 receiver that holds the TB ACKs the retransmission when its SCI-2A enables feedback; with --reAckDecoded (default true) it does.  Residual DTX is 3GPP-correct (4.4): "
                              "a member whose SCI-2 fails (status 2) cannot ACK; the eval cannot observe an SCI-1 miss of an already-decoded member (status 0 either way), hence the plausibility bound"));
            const Prop ps = Wilson(fx.wastedTx, fx.totalTx);
            T.Info(pre + "transmissions sent after every member already holds the TB (any cause), after the fix",
                   Opt().Fnd("F25").Obs(FmtI(fx.wastedTx) + " of " + FmtI(fx.totalTx) + " transmissions (" + Fmt(100.0 * ps.p, 3) + " % [" + Fmt(100.0 * ps.lo, 3) + ", " + Fmt(100.0 * ps.hi, 3) + "]), "
                                        + Fmt(fx.wastedSub, 6) + " of " + Fmt(fx.totalSub, 6) + " subchannel-slots (" + Fmt(fx.totalSub > 0 ? 100.0 * fx.wastedSub / fx.totalSub : 0.0, 3) + " %); "
                                        + FmtI(fx.tbsWasted) + " of " + FmtI(fx.tbs) + " TBs carry one"));
            if (!v1.empty())
            {
                const F25Stat f0 = F25Analyse(Select(v1, arm), arm, rs == "CORE");
                if (f0.runs > 0)
                {
                    f25row(rs, arm, "before", f0);
                    const Prop p0 = Wilson(f0.wastedTx, f0.totalTx);
                    T.Info(pre + "F25 before the fix (reference tests/out_prefix_v1)",
                           Opt().Fnd("F25").Obs(FmtI(f0.wastedTx) + " of " + FmtI(f0.totalTx) + " transmissions sent after every member held the TB (" + Fmt(100.0 * p0.p, 3) + " %), " + Fmt(f0.wastedSub, 6) + " of "
                                                + Fmt(f0.totalSub, 6) + " subchannel-slots; " + FmtI(f0.tbsWasted) + " of " + FmtI(f0.tbs) + " TBs ran on after everyone held the TB; DTX-only retriggers " + FmtI(f0.wasted) + " of "
                                                + FmtI(f0.opps) + " opportunities (" + FmtI(f0.succ) + " decided success)")
                               .Note("pre-fix behaviour: nr-spectrum-phy.cc:2723-2729 skipped the feedback of an already decoded TB, so the leader's all-ACK rule was unreachable once a retransmission was needed"));
                }
            }
        }
    }

    // ---- SYN_DTX: the cost of DTX = NACK ------------------------------------------------------------------------------------
    {
        T.AddRunset("SYN_DTX");
        long tbs = 0, full = 0, redundantTx = 0, redundantSubch = 0, totalSubch = 0;
        for (const auto& rr : Select(LoadManifest("SYN_DTX"), "R3"))
        {
            Run r;
            if (!r.Load(rr) || !r.hasAtt)
            {
                continue;
            }
            T.AddNRuns(1);
            for (const TbView& tv : WindowTbViews(r))
            {
                ++tbs;
                full += (static_cast<int>(tv.leader.size()) == r.maxNumTx);
                for (size_t k = 0; k < tv.leader.size(); ++k)
                {
                    totalSubch += tv.leader[k]->rbLen / kSubchPrbs;
                    if (k >= 1)
                    {
                        bool all7 = true;
                        for (int m = 1; m <= 7; ++m)
                        {
                            all7 = all7 && r.DecodedBy(*tv.tb, static_cast<int>(k) - 1, m);
                        }
                        if (all7)
                        {
                            ++redundantTx;
                            redundantSubch += tv.leader[k]->rbLen / kSubchPrbs;
                        }
                    }
                }
            }
        }
        if (tbs == 0)
        {
            T.Skip("SYN_DTX/R3", "no runs");
        }
        else
        {
            T.Check("SYN_DTX R3: member 8 is silent on every TB, so every TB uses all maxNumTx transmissions (DTX = NACK)", full == tbs,
                    Opt().Exp(FmtI(tbs) + " TBs with 3 transmissions").Obs(FmtI(full)).Tol("0").Sev("H").Ref("TS 38.321 5.22.1.3 [V]"));
            T.Info("SYN_DTX R3: cost of an unreachable member under Option 2",
                   Opt().Obs(FmtI(redundantTx) + " retransmissions were redundant for the 7 reachable members (all had decoded); " +
                             FmtI(redundantSubch) + " of " + FmtI(totalSubch) + " leader subchannel-slots (" +
                             Fmt(totalSubch ? 100.0 * redundantSubch / totalSubch : 0.0, 4) + " %)")
                       .Note("3GPP-correct behaviour of Option 2 with a member out of reach, reported as the cost of the option"));
            T.Metric("dtx_cost_redundant_subch_share", totalSubch ? static_cast<double>(redundantSubch) / totalSubch : 0.0);
            T.Artefact("results/B03_dtx_cost.csv");
            CsvWriter w(TestsRoot() + "/results/B03_dtx_cost.csv", "metric,value");
            w.Row({"tbs", FmtI(tbs)});
            w.Row({"tbs_with_maxNumTx", FmtI(full)});
            w.Row({"redundant_tx", FmtI(redundantTx)});
            w.Row({"redundant_subch_slots", FmtI(redundantSubch)});
            w.Row({"total_leader_subch_slots", FmtI(totalSubch)});
        }
    }

    // ---- (M) stock MAC: the first ACK cancels the retransmissions (DV2 / F12) --------------------------------------------------------
    {
        const FailDistance fd = FindFailingDistance();
        if (!fd.found)
        {
            T.Skip("micro/DV2 stock first-ACK-cancels", "no probed distance has >= 90% corrupt PSSCH with >= 90% decoded SCI: " + fd.table);
        }
        else
        {
            MicroCfg c;
            c.rxDistM = {20.0, fd.distM};
            c.nPackets = 100;
            c.intervalMs = 40.0;
            c.maxNumTx = 3;
            MicroLog lg;
            std::string err;
            if (!RunMicro(c, lg, &err))
            {
                T.Fail("micro/DV2 setup", Opt().Obs(err));
            }
            else
            {
                const double slotS = 1e-3 / lg.slotsPerSubframe;
                // TBs from the TX log: a new TB at every ndi == 1
                struct TbM
                {
                    std::vector<size_t> tx;
                };
                std::vector<TbM> tbs;
                for (size_t i = 0; i < lg.tx.size(); ++i)
                {
                    if (lg.tx[i].ndi == 1 || tbs.empty())
                    {
                        tbs.emplace_back();
                    }
                    tbs.back().tx.push_back(i);
                }
                long withNack = 0, cancelled = 0, retxed = 0, ackFromRx1 = 0, total = 0;
                for (const auto& tb : tbs)
                {
                    ++total;
                    const auto& t1 = lg.tx[tb.tx[0]];
                    const double tEnd = tb.tx.size() > 1 ? lg.tx[tb.tx[1]].t : t1.t + 30 * slotS;
                    bool nack = false, ack = false;
                    for (const auto& f : lg.fbRx)
                    {
                        if (f.harq == t1.harq && f.t > t1.t && f.t <= tEnd)
                        {
                            if (!f.ack && f.rxRnti == lg.txRnti[2])
                            {
                                nack = true;
                            }
                            if (f.ack && f.rxRnti == lg.txRnti[1])
                            {
                                ack = true;
                            }
                        }
                    }
                    if (nack)
                    {
                        ++withNack;
                        ackFromRx1 += ack ? 1 : 0;
                        if (tb.tx.size() == 1)
                        {
                            ++cancelled;
                        }
                        else
                        {
                            ++retxed;
                        }
                    }
                }
                T.Info("micro: setup (RX1 20 m always decodes, RX2 " + Fmt(fd.distM, 4) + " m corrupt in " + Fmt(100 * fd.corruptFrac, 4) + " % of " + FmtI(fd.events) + " receptions)",
                       Opt().Obs(fd.table));
                T.CheckXfail("micro/stock MAC: RX2's NACK triggers the retransmission although RX1 ACKed (HARQ Option 2, Groupcast cast type)",
                             withNack > 0 && cancelled == 0,
                             Opt().Fnd("DV2").Sev("M")
                                 .Exp("a NACK from any member keeps the retransmissions alive (3GPP Option 2: success only if all ACK)")
                                 .Obs(FmtI(withNack) + " TBs were NACKed by RX2 after the first transmission; " + FmtI(cancelled) +
                                      " ended with a single transmission (cancelled by RX1's ACK), " + FmtI(retxed) + " were retransmitted; RX1 ACKed " + FmtI(ackFromRx1) + " of them")
                                 .Note("stock nr-sl-ue-mac-harq.cc cancels the pending retransmissions on the first ACK; the study bypasses this "
                                       "(UserWrap::ReceivePsfch does not forward member PSFCH in Option 2; the controller delivers a synthetic "
                                       "ACK only on all-ACK), verified in the SYN_MARGINAL checks above"));
                T.Metric("micro_dv2_nacked_tbs", static_cast<double>(withNack));
                T.Metric("micro_dv2_cancelled_tbs", static_cast<double>(cancelled));
            }
        }
    }
    const std::string art = V.Write("B03_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
