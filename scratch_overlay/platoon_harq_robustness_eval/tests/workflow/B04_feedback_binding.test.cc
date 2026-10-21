// B04_feedback_binding (W: SYN_MARGINAL, CORE; R2, R3, R5_S1_H2, R6_S1_H2) and (M): every NACK is bound to exactly one
// platoon PSSCH (same transmitter RNTI and HARQ id, decode time inside the PSSCH slot); the eval's 5 ms attribution
// (F17) agrees; the stock MAC binds feedback by (txRnti, harqId) only (DV4/F29): characterised with a stale ACK.
#include "common/csv.h"
#include "common/harness.h"
#include "common/micro_sl.h"
#include "common/run_outputs.h"
#include "common/runsets.h"

#include <algorithm>
#include <map>

using namespace ptest;

int
main()
{
    TestReport T("B04", "feedback_binding", "B");
    T.Summary("Every NACK of _nack.csv maps to exactly one transmission (leader or helper copy) of the right HARQ process, the "
              "eval attributes the same NACKs to the same transmissions (F17), and the NR MAC itself would let a stale ACK "
              "cancel a newer TB on the same HARQ id (DV4/F29).");
    T.AddRef("TS 38.321 5.22.1.3 (HARQ feedback is bound to the HARQ process of the PSSCH it answers) [V]");
    T.AddRef("TEST_PLAN DV4 / F17 / F29");
    ViolationLog V;

    const std::vector<std::string> arms = {"R2", "R3", "R5_S1_H2", "R6_S1_H2"};
    for (const std::string rs : {"SYN_MARGINAL", "CORE"})
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
        for (const std::string& arm : arms)
        {
            long ambiguousP2 = 0, nacksP2 = 0, mismatchedTbP2 = 0;
            long runs = 0, nacks = 0, unmatched = 0, ambiguous = 0, txsCompared = 0, mism = 0, evalDropped = 0, evalExtra = 0,
                 evalTotal = 0, mineTotal = 0, mismatchedTb = 0;
            for (const auto& rr : Select(all, arm))
            {
                Run r;
                if (!r.Load(rr) || !r.hasAtt || !r.hasAlltx)
                {
                    continue;
                }
                ++runs;
                T.AddNRuns(1);
                nacks += static_cast<long>(r.nacks.size());
                unmatched += r.nackUnmatched;
                if (rr.traffic.rfind("periodic2", 0) == 0)
                {
                    ambiguousP2 += r.nackAmbiguous;
                    nacksP2 += static_cast<long>(r.nacks.size());
                }
                else
                {
                    ambiguous += r.nackAmbiguous;
                }
                if (r.nackUnmatched)
                {
                    V.Add(arm + " unmatched NACK", rs + "/" + rr.tag + ": " + FmtI(r.nackUnmatched));
                }
                // eval attribution (n_nack_sent per transmission) vs the independent attribution (distinct NACKing members)
                for (const PTx& x : r.txs)
                {
                    if (x.node < 0)
                    {
                        continue;
                    }
                    ++txsCompared;
                    const int mine = __builtin_popcount(x.nackMask);
                    mineTotal += mine;
                    evalTotal += x.nNackSent;
                    if (mine != x.nNackSent)
                    {
                        ++mism;
                        if (mine > x.nNackSent)
                        {
                            evalDropped += mine - x.nNackSent;
                        }
                        else
                        {
                            evalExtra += x.nNackSent - mine;
                        }
                        if (V.Count(arm + " eval vs independent NACK attribution") < 20)
                        {
                            V.Add(arm + " eval vs independent NACK attribution",
                                  rs + "/" + rr.tag + " slot " + FmtI(x.slot) + " mine " + std::to_string(mine) + " eval " + std::to_string(x.nNackSent));
                        }
                    }
                    // the NACK's TB is the one whose decision counted it
                    if (x.nackMask != 0 && x.nNack == 0 && x.side == 0 && x.decision != 2)
                    {
                        ++(rr.traffic.rfind("periodic2", 0) == 0 ? mismatchedTbP2 : mismatchedTb);
                        V.Add(arm + " NACK attributed to a transmission whose decision did not count it",
                              rs + "/" + rr.tag + " slot " + FmtI(x.slot) + " nackMask " + std::to_string(x.nackMask) + " n_nack " +
                                  std::to_string(x.nNack) + " sent " + std::to_string(x.nNackSent) + " decision " + std::to_string(x.decision) +
                                  " harq " + std::to_string(x.harq));
                    }
                }
            }
            if (runs == 0)
            {
                continue;
            }
            const std::string pre = rs + " " + arm + ": ";
            T.Check(pre + "every NACK maps to a transmission (same RNTI + HARQ id, decoded inside its slot)", unmatched == 0,
                    Opt().Exp("0 unmatched of " + FmtI(nacks)).Obs(FmtI(unmatched)).Tol("0").Sev("H").Fnd("F17"));
            T.Check(pre + "no NACK is ambiguous (two candidate TBs with the same HARQ id within 32 slots), periodic1tb/aperiodic1tb", ambiguous == 0,
                    Opt().Exp("0").Obs(FmtI(ambiguous) + " of " + FmtI(nacks - nacksP2)).Tol("0").Sev("M").Fnd("F29"));
            if (nacksP2 > 0)
            {
                T.CheckXfail(pre + "no NACK is ambiguous, periodic2tb (10 ms period = 20 slots < 32)", ambiguousP2 == 0,
                             Opt().Fnd("F29").Sev("M").Exp("0 ambiguous")
                                 .Obs(FmtI(ambiguousP2) + " of " + FmtI(nacksP2) + " NACKs have two TBs of the same HARQ id within 32 slots")
                                 .Note("SPS keeps one HARQ process across periods, so with a 20-slot period the stock binding (txRnti, harqId, dstL2Id; no NDI/slot check) "
                                       "cannot tell consecutive TBs apart; the eval resolves it by time (decode inside the PSSCH slot)"));
            }
            T.Check(pre + "a NACK attributed to a leader transmission was counted in that transmission's decision (n_nack >= 1), periodic1tb/aperiodic1tb", mismatchedTb == 0,
                    Opt().Exp("0").Obs(FmtI(mismatchedTb)).Tol("0").Sev("H").Note("the only case is in R5 (helper-NACK path, same family as B02 DV1: a NACK sent by a member is not credited to the leader decision). Controller code: NackOnlyHarqController::OnPsfch (nack_only_harq.cc ~346-446) credits every feedback that passes the dst / txRnti filter (line 352) and finds a pending transmission (best) by exact binding (~381-396; an unbound feedback goes to nFbLate), so a NACK that is sent but not credited either never reaches OnPsfch or is bound to another transmission; fb_late is 0 in those runs; not isolated without a diagnostic run (open, simulator side)"));
            if (arm == "R3" || mismatchedTbP2 > 0)
            {
                T.CheckXfail(pre + "a NACK attributed to a leader transmission was counted in that transmission's decision, periodic2tb", mismatchedTbP2 == 0,
                             Opt().Fnd("F29").Sev("M").Exp("0")
                                 .Obs(FmtI(mismatchedTbP2) + " NACKs were sent for a transmission whose decision recorded n_nack = 0")
                                 .Note("the controller binds a PSFCH to 'the pending transmission of this HARQ id with the earliest decision time'; with SPS the "
                                       "HARQ id is reused every 20 slots (periodic2tb), so a feedback can be credited to the neighbouring TB (see the artefact)"));
            }
            T.Check(pre + "eval's own 5 ms attribution equals the independent one (n_nack_sent per transmission)", mism == 0,
                    Opt().Exp("0 of " + FmtI(txsCompared) + " transmissions differ")
                        .Obs(FmtI(mism) + " differ; independent total " + FmtI(mineTotal) + " vs eval total " + FmtI(evalTotal) +
                             "; NACKs the eval dropped " + FmtI(evalDropped) + ", extra " + FmtI(evalExtra))
                        .Tol("0").Sev("M").Fnd("F17"));
        }
    }

    // ---- (M) stale ACK on a reused HARQ id (stock MAC binding) ----------------------------------------------------------------
    {
        const FailDistance fd = FindFailingDistance();
        if (!fd.found)
        {
            T.Skip("micro/DV4 stale ACK", "no always-failing distance found: " + fd.table);
        }
        else
        {
            MicroCfg base;
            base.rxDistM = {fd.distM}; // a single failing receiver: its NACKs keep the retransmissions alive (no other ACK to cancel them)
            base.nPackets = 100;
            base.intervalMs = 40.0;
            base.maxNumTx = 3;
            MicroLog ctrl;
            std::string err;
            if (!RunMicro(base, ctrl, &err))
            {
                T.Fail("micro/DV4 control run", Opt().Obs(err));
            }
            else
            {
                // first TB with >= 2 transmissions in the control run
                int tbStart = -1;
                std::vector<std::vector<size_t>> tbs;
                for (size_t i = 0; i < ctrl.tx.size(); ++i)
                {
                    if (ctrl.tx[i].ndi == 1 || tbs.empty())
                    {
                        tbs.emplace_back();
                    }
                    tbs.back().push_back(i);
                }
                size_t idx = 0;
                for (; idx < tbs.size(); ++idx)
                {
                    if (tbs[idx].size() >= 2 && ctrl.tx[tbs[idx][0]].t > 3.0)
                    {
                        break;
                    }
                }
                if (idx >= tbs.size())
                {
                    T.Skip("micro/DV4 stale ACK", "no retransmitted TB in the control run");
                }
                else
                {
                    const auto& t1 = ctrl.tx[tbs[idx][0]];
                    const double slotS = 1e-3 / ctrl.slotsPerSubframe;
                    MicroCfg inj = base;
                    inj.inject.push_back({t1.t + 1.5 * slotS, t1.harq, true, 0});
                    MicroLog lg;
                    if (!RunMicro(inj, lg, &err))
                    {
                        T.Fail("micro/DV4 injection run", Opt().Obs(err));
                    }
                    else
                    {
                        // the same TB in the injection run: identical until the injection
                        size_t nCtrl = tbs[idx].size(), nInj = 0;
                        for (const auto& x : lg.tx)
                        {
                            if (std::fabs(x.t - t1.t) < 1e-9)
                            {
                                nInj = 1;
                            }
                            else if (nInj >= 1 && x.harq == t1.harq && x.ndi == 0 && x.t > t1.t && x.t < t1.t + 40 * slotS)
                            {
                                ++nInj;
                            }
                        }
                        T.CheckXfail("micro/stock MAC: a stale ACK for HARQ id h cancels the pending retransmissions of the newer TB on h",
                                     nInj >= nCtrl,
                                     Opt().Fnd("F29").Sev("M")
                                         .Exp("the ACK of an old TB is ignored (feedback is bound to the TB, e.g. by NDI/slot)")
                                         .Obs("TB at t=" + Fmt(t1.t, 7) + " s (HARQ id " + std::to_string(t1.harq) + "): " + FmtI(static_cast<long>(nCtrl)) +
                                              " transmissions without injection, " + FmtI(static_cast<long>(nInj)) + " with a synthetic ACK injected 1.5 slots after its first transmission")
                                         .Note("DV4: the stock MAC binds feedback by (txRnti, harqProcessId, dstL2Id) only; a late ACK that hits a reused HARQ id "
                                               "cancels the newer TB's retransmissions. The study's HARQ ids are reused after at most 16 TBs; B05 measures "
                                               "the stale rate in the study runs"));
                        T.Metric("micro_dv4_tx_control", static_cast<double>(nCtrl));
                        T.Metric("micro_dv4_tx_injected", static_cast<double>(nInj));
                    }
                }
            }
        }
    }
    const std::string art = V.Write("B04_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
