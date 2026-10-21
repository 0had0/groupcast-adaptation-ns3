// C03_option2_helper_silence (W: SYN_NEAR, SYN_MARGINAL, SYN_DTX; C: CORE R6_S1_H2): the named helper never ACKs (its ACK is suppressed
// at the receiver, it listens instead); its NACK makes the leader reclaim the next handed-over slot; the leader declares success
// only if every non-helper member ACKed; blind spot of a helper that missed the SCI (F07).
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <cstdlib>
#include <map>

using namespace ptest;

int
main()
{
    TestReport T("C03", "option2_helper_silence", "C");
    T.Summary("R6: the named helper sends no ACK (suppressed == its decodes), its NACK triggers a reclaim of a handed slot, "
              "success requires every other member's ACK; a helper that misses the SCI stays silent and is never served (F07).");
    T.AddRef("README (custom design): silent helper + NACK reclaim (nack_only_harq.cc:392-415, 515-541)");
    T.AddRef("TS 38.321 5.22.1.3: DTX = NACK in Option 2 [V]");
    ViolationLog V;

    for (const std::string rs : {"SYN_NEAR", "SYN_MARGINAL", "SYN_DTX", "CORE"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty())
        {
            if (rs == "CORE")
            {
                T.Skip("CORE quick data", "CORE manifest not present (campaign stage not run yet)");
            }
            continue;
        }
        T.AddRunset(rs);
        long runs = 0, staleRun = 0, ackRx = 0, suppExp = 0, suppRun = 0, reAckUpper = 0, hDecWin = 0, hDecRun = 0, nackExp = 0, nackRun = 0, reclaim = 0, noSlot = 0, leaderRetx = 0,
             falseAck = 0, hCorrupt = 0, hNackSent = 0, hNackNotSent = 0, hSci2 = 0, hNackLost = 0, txs = 0, leaderRetxP2 = 0, reclaimP2 = 0, f07Tbs = 0, f07Success = 0, namedTbs = 0, h0b = 0;
        std::vector<double> helperPrr, worstOtherPrr;
        for (const auto& rr : Select(all, "R6_S1_H2"))
        {
            Run r;
            if (!r.Load(rr) || !r.hasAtt || !r.hasAlltx || !r.hasHsel)
            {
                continue;
            }
            ++runs;
            T.AddNRuns(1);
            ackRx += static_cast<long>(r.R("helper_ack_rx", 0));
            suppRun += static_cast<long>(r.R("helper_ack_suppressed", 0));
            hDecRun += static_cast<long>(r.R("helper_decoded_tx", 0));
            nackRun += static_cast<long>(r.R("helper_nack", 0));
            staleRun += static_cast<long>(r.R("helper_nack_stale", 0));
            hCorrupt += static_cast<long>(r.R("helper_corrupt_tx", 0));
            hNackSent += static_cast<long>(r.R("helper_nack_sent", 0));
            hNackNotSent += static_cast<long>(r.R("helper_nack_not_sent", 0));
            hSci2 += static_cast<long>(r.R("helper_corrupt_sci2", 0));
            hNackLost += static_cast<long>(r.R("helper_nack_lost", 0));
            reclaim += static_cast<long>(r.R("helper_reclaim", 0));
            const bool p2 = (rr.traffic.rfind("periodic2", 0) == 0);
            if (p2)
            {
                reclaimP2 += static_cast<long>(r.R("helper_reclaim", 0));
            }
            noSlot += static_cast<long>(r.R("helper_nack_no_slot", 0));
            const std::string where = rs + "/" + rr.tag;
            // recount over the whole run: leader transmissions with a named helper
            long suppThis = 0;
            std::map<int64_t, int> helperOfTb;
            for (const PTx& x : r.txs)
            {
                if (x.side != 0 || x.txInTb != 1)
                {
                    continue;
                }
                helperOfTb[x.tb] = r.HelperOf(x);
            }
            for (const PTx& x : r.txs)
            {
                if (x.side != 0)
                {
                    continue;
                }
                auto it = helperOfTb.find(x.tb);
                if (it == helperOfTb.end())
                {
                    continue;
                }
                const int h = it->second;
                if (h >= 1 && h <= kMembers && x.status[h - 1] != 1 && x.status[h - 1] != 2 && x.txInTb > 1)
                {
                    // possible re-ACK of a retransmission of a TB the helper already holds (status 0 = no decode attempt: the eval cannot tell a re-ACK from an SCI-1 miss)
                    auto tbIt2 = r.tbs.find(x.tb);
                    if (tbIt2 != r.tbs.end())
                    {
                        const int u = static_cast<int>(&x - &r.txs[0]);
                        for (size_t q = 1; q < tbIt2->second.tx.size(); ++q)
                        {
                            if (tbIt2->second.tx[q] == u)
                            {
                                reAckUpper += r.DecodedBy(tbIt2->second, static_cast<int>(q) - 1, h) ? 1 : 0;
                                break;
                            }
                        }
                    }
                }
                if (h >= 1 && h <= kMembers && x.status[h - 1] == 1)
                {
                    ++suppThis;
                    if (r.InWin(x.t))
                    {
                        ++hDecWin;
                    }
                }
                if (x.txInTb > 1)
                {
                    // the helper_* counters run over the KPI window and are incremented when the helper's NACK is received (a few ms before the
                    // retransmission), so a leader retransmission is attributed to the transmission it follows: counted when that one lies in the window
                    const PTx* prevTx = nullptr;
                    auto tbIt = r.tbs.find(x.tb);
                    if (tbIt != r.tbs.end())
                    {
                        for (int u : tbIt->second.tx)
                        {
                            const PTx& c = r.txs[u];
                            if (c.t < x.t - 1e-9 && (!prevTx || c.t > prevTx->t))
                            {
                                prevTx = &c;
                            }
                        }
                    }
                    if (prevTx && r.InWin(prevTx->t))
                    {
                        ++leaderRetx;
                        leaderRetxP2 += p2 ? 1 : 0;
                    }
                }
                if (r.InWin(x.t))
                {
                    ++txs;
                }
            }
            suppExp += suppThis;
            // helper NACKs in the window: NACK rows matched to leader txs in the window by the named helper
            for (size_t i = 0; i < r.nacks.size(); ++i)
            {
                const int u = r.nackTx[i];
                if (u < 0)
                {
                    continue;
                }
                const PTx& x = r.txs[u];
                if (x.side != 0 || !r.InWin(x.t))
                {
                    continue;
                }
                auto it = helperOfTb.find(x.tb);
                if (it != helperOfTb.end() && it->second == r.nacks[i].member)
                {
                    ++nackExp;
                }
            }
            // leader success rule: decision 1 only if every non-helper member decoded (cumulative)
            for (const TbView& tv : WindowTbViews(r))
            {
                ++namedTbs;
                const int h = tv.helperNode;
                for (size_t k = 0; k < tv.all.size(); ++k)
                {
                    const PTx& x = *tv.all[k];
                    if (x.side == 0 && x.decision == 1)
                    {
                        bool ok = true;
                        for (int m = 1; m <= kMembers; ++m)
                        {
                            if (m != h)
                            {
                                ok = ok && r.DecodedBy(*tv.tb, static_cast<int>(k), m);
                            }
                        }
                        if (!ok)
                        {
                            ++falseAck;
                            V.Add("R6 false ACK", where + " tb " + FmtI(tv.id));
                        }
                    }
                }
                // blind spot: helper missed the SCI (status 0 at the initial) and the leader declared success
                if (h >= 1 && h <= kMembers && tv.initial->status[h - 1] == 0)
                {
                    ++f07Tbs;
                    bool succ = false;
                    for (const PTx* x : tv.leader) succ = succ || x->decision == 1;
                    f07Success += succ ? 1 : 0;
                    bool nonHelperNeeds = false;
                    for (int m = 1; m <= kMembers; ++m)
                    {
                        if (m != h) nonHelperNeeds = nonHelperNeeds || tv.initial->status[m - 1] != 1;
                    }
                    h0b += nonHelperNeeds ? 1 : 0;
                }
            }
            const int h0 = r.helperNodeStatic;
            (void)h0;
            // helper PRR vs the worst non-helper member (use the most frequently named helper)
            std::map<int, int> cnt;
            for (const auto& [tb, h] : helperOfTb) cnt[h]++;
            int hm = 0, best = 0;
            for (const auto& [h, n] : cnt) { if (n > best) { best = n; hm = h; } }
            if (hm >= 1 && hm <= kMembers)
            {
                helperPrr.push_back(r.MemberCol("prr", hm));
                double w = 2;
                for (int m = 1; m <= kMembers; ++m) if (m != hm) w = std::min(w, r.MemberCol("prr", m));
                worstOtherPrr.push_back(w);
            }
        }
        if (runs == 0)
        {
            continue;
        }
        const std::string pre = rs + " R6: ";
        T.Check(pre + "helper_ack_rx == 0 (the named helper never ACKs)", ackRx == 0, Opt().Exp("0").Obs(FmtI(ackRx)).Tol("0").Sev("H"));
        T.Check(pre + "helper_ack_suppressed >= helper first decodes on its TBs and <= first decodes + possible re-ACKs (whole run)", suppRun >= suppExp && suppRun <= suppExp + reAckUpper,
                Opt().Exp(FmtI(suppExp) + " .. " + FmtI(suppExp + reAckUpper)).Obs(FmtI(suppRun)).Tol("0").Sev("H")
                    .Note("after the F25 fix the counter also counts the helper's suppressed RE-ACKs of retransmissions of a TB it already holds (platoon sim Option2HelperSilenceShim counts every ACK feedback of the named helper). "
                          "Recount: first decodes = leader transmissions whose named helper has status 1; re-ACK opportunities (upper bound) = leader retransmissions after the helper held the TB with status 0 (an SCI-1 miss is indistinguishable in _att)"));
        T.Info(pre + "helper_ack_suppressed re-ACKs: counter minus first decodes vs possible re-ACK opportunities",
               Opt().Fnd("F25").Obs(FmtI(suppRun - suppExp) + " suppressed re-ACKs of " + FmtI(reAckUpper) + " opportunities (" + Fmt(reAckUpper > 0 ? 100.0 * (suppRun - suppExp) / reAckUpper : 0.0, 3) + " %)"));
        T.Check(pre + "helper_decoded_tx == recount (KPI window)", std::labs(hDecRun - hDecWin) <= runs, Opt().Exp(FmtI(hDecWin)).Obs(FmtI(hDecRun)).Tol("+-1 per run at the window edge").Sev("M").Note("roundabout whole-trace runs end with the trace: one transmission in the last slot can fall on either side of the KPI window end (same effect as E01)"));
        T.Check(pre + "helper NACKs credited by the leader (+ helper_nack_stale) == NACK rows of the named helper attributed to window transmissions", nackRun + staleRun == nackExp,
                Opt().Exp(FmtI(nackExp)).Obs(FmtI(nackRun) + " credited + " + FmtI(staleRun) + " stale (not credited, no reclaim)").Tol("0").Sev("H").Fnd("F30")
                    .Note("TEST_PLAN 0.2: a helper NACK for a TB its HARQ id no longer carries is counted in helper_nack_stale and does not reclaim"));
        if (hCorrupt > 0)
        {
            T.Check(pre + "every corrupt TB of the named helper without a NACK is an SCI-2A failure (helper_nack_not_sent == helper_corrupt_sci2; sent + not sent == corrupt)",
                    hNackNotSent == hSci2 && hNackSent + hNackNotSent == hCorrupt,
                    Opt().Exp("not_sent == corrupt_sci2, sent + not_sent == corrupt_tx").Obs("corrupt_tx " + FmtI(hCorrupt) + ", nack_sent " + FmtI(hNackSent) + ", nack_not_sent " + FmtI(hNackNotSent) +
                                                                                              ", corrupt_sci2 " + FmtI(hSci2) + ", nack_lost " + FmtI(hNackLost))
                        .Tol("0").Sev("L")
                        .Note("a receiver whose SCI-2A is corrupt cannot send feedback; " + Fmt(100.0 * hNackNotSent / hCorrupt, 3) +
                              " % of the helper's corrupt receptions produce no NACK. The same applies to every member under Option 1 (G02 q)"));
        }
        T.Check(pre + "helper_reclaim + helper_nack_no_slot == helper NACKs", reclaim + noSlot == nackRun,
                Opt().Exp(FmtI(nackRun)).Obs(FmtI(reclaim) + " + " + FmtI(noSlot)).Tol("0").Sev("H"));
        T.Check(pre + "after a reclaim the leader transmits in the reclaimed slot (leader retransmissions == reclaims), periodic1tb/aperiodic1tb",
                (leaderRetx - leaderRetxP2) == (reclaim - reclaimP2),
                Opt().Exp(FmtI(reclaim - reclaimP2) + " leader retransmissions").Obs(FmtI(leaderRetx - leaderRetxP2)).Tol("0").Sev("H")
                    .Note("in S1 the leader keeps LeaderTxCount=1, so any leader transmission with tx_in_tb > 1 is a reclaimed slot"));
        if (reclaimP2 > 0 || leaderRetxP2 > 0)
        {
            T.CheckXfail(pre + "periodic2tb: leader retransmissions == reclaims", leaderRetxP2 == reclaimP2,
                         Opt().Fnd("F08").Sev("M").Exp(FmtI(reclaimP2)).Obs(FmtI(leaderRetxP2))
                             .Note("HARQ-id sharing of overlapping 10 ms periods: a reclaim of one TB is counted on the neighbouring TB (B05)"));
        }
        T.Check(pre + "leader success rule: decision 1 only when every non-helper member decoded", falseAck == 0,
                Opt().Exp("0 false ACKs over " + FmtI(txs) + " transmissions").Obs(FmtI(falseAck)).Tol("0").Sev("H").Fnd("F31")
                    .Note("F31 (new): the F25 fix ACKs every retransmission of a TB the receiver thinks it holds (nr-spectrum-phy.cc IsPrevDecoded, keyed by (txRnti, HARQ id)).  RemovePrevDecoded runs only after SCI-2A of an ndi=1 transmission decoded OK, so a receiver whose SCI-2A of the new TB failed (status 2, no NACK) keeps the flag of the PREVIOUS TB on the same HARQ id and ACKs the retransmission although it never decoded the TB: the leader declares success (decision 1) with a member lacking the TB.  The same stale flag existed before the fix: such a member dropped every retransmission silently (v1: 0 of 254 R2 and 0 of 256 R3 member-TB pairs with SCI-2 failure at the initial and the previous TB decoded ever decoded the TB later); the fix turns the silent drop into a false ACK"));
        // F07
        const MeanCI hp = MeanCi(helperPrr), wo = MeanCi(worstOtherPrr);
        {
            const bool helperBelowWorst = helperPrr.size() >= 1 && Mean(helperPrr) < Mean(worstOtherPrr) - 1e-9;
            T.CheckXfail(pre + "F07: a helper that missed the SCI is not served (DTX should be NACK)", f07Success == 0,
                         Opt().Fnd("F07").Sev("M").Exp("0 TBs declared successful although the named helper missed the SCI")
                             .Obs(FmtI(f07Success) + " of " + FmtI(f07Tbs) + " TBs where the helper missed the SCI were declared successful (" + FmtI(h0b) +
                                  " of them also had a non-helper member in need); helper PRR " + Fmt(Mean(helperPrr), 4) + " vs worst non-helper " + Fmt(Mean(worstOtherPrr), 4))
                             .Note("blind spot of the silent helper: its silence counts as 'has it'"));
            T.Check(pre + "helper PRR is not below the worst non-helper member's PRR (FAIL criterion of Q7)", !helperBelowWorst,
                    Opt().Exp("helper PRR >= worst other").Obs("helper " + (hp.ok() ? FmtCi(hp.mean, hp.lo, hp.hi, 4) : Fmt(hp.mean, 4)) + " vs worst other " +
                                                              (wo.ok() ? FmtCi(wo.mean, wo.lo, wo.hi, 4) : Fmt(wo.mean, 4))).Sev("M").Fnd("F07"));
        }
        T.Metric(rs + "_f07_tbs", static_cast<double>(f07Tbs));
        T.Metric(rs + "_f07_declared_success", static_cast<double>(f07Success));
        (void)namedTbs;
    }
    const std::string art = V.Write("C03_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
