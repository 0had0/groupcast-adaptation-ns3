// B06_sps_harq_interaction (W: SYN_SPS; C: CORE): re-implementation of check_sps.py for Option-1 and Option-2 leaders under SB-SPS
// plus: a synthetic ACK never cancels the next period's TB (F20) and every SPS period that had data has its ndi=1 transmission.
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <algorithm>
#include <map>

using namespace ptest;

int
main()
{
    TestReport T("B06", "sps_harq_interaction", "B");
    T.Summary("Under SB-SPS the implicit/all-ACK of one period never removes the next period's TB: no retransmission after an "
              "ACK or without a NACK, every packet gets its new-data transmission (F20), reservations keep the +RRI spacing.");
    T.AddRef("TS 38.321 5.22.1.1 (SL resource reservation counter), 5.22.1.3 (HARQ feedback handling) [V]");
    T.AddRef("TEST_PLAN F20; check_sps.py (Option-1 wrapper checks)");
    ViolationLog V;

    for (const std::string rs : {"SYN_SPS", "CORE"})
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
        for (const std::string arm : {"R2", "R3", "R5_S1_H2", "R6_S1_H2"})
        {
            long runs = 0, retxAfterAck = 0, retxNoNack = 0, ackTbs = 0, ackKept = 0, ackResel = 0, perKeptNum = 0, perKeptDen = 0,
                 missingNew = 0, pktsTot = 0, newTbTot = 0, notTx = 0, ackNoRetxLeft = 0, tbsTot = 0;
            for (const auto& rr : Select(all, arm))
            {
                if (!rr.leaderSps)
                {
                    continue;
                }
                Run r;
                if (!r.Load(rr))
                {
                    continue;
                }
                ++runs;
                T.AddNRuns(1);
                const std::string where = rs + "/" + rr.arm + "/" + rr.tag;
                const int64_t rriSlots = static_cast<int64_t>(std::llround(RriMsOfTraffic(rr.traffic) / 1000.0 / kSlotS));
                const bool opt1 = (arm == "R2" || arm == "R5_S1_H2");
                // leader TBs in order
                std::vector<const Tb*> order;
                std::map<int, int64_t> lastOfHarq;
                std::map<int64_t, int64_t> nextTb; // tb -> next tb on the same harq id
                std::vector<std::pair<int64_t, const PTx*>> firsts;
                for (const PTx& x : r.txs)
                {
                    if (x.side == 0 && x.txInTb == 1)
                    {
                        firsts.push_back({x.tb, &x});
                    }
                }
                std::map<int, int64_t> prev;
                for (const auto& [id, x] : firsts)
                {
                    if (prev.count(x->harq))
                    {
                        nextTb[prev[x->harq]] = id;
                    }
                    prev[x->harq] = id;
                }
                std::map<int64_t, const PTx*> first;
                for (const auto& [id, x] : firsts) first[id] = x;
                for (const TbView& tv : WindowTbViews(r))
                {
                    ++tbsTot;
                    // retransmission after an ACK / without a NACK (leader-side successive transmissions; for helper arms the
                    // relayed copies are covered by C02)
                    for (size_t k = 0; k + 1 < tv.leader.size(); ++k)
                    {
                        const PTx& a = *tv.leader[k];
                        if (a.decision == 1)
                        {
                            ++retxAfterAck;
                            V.Add(arm + " retransmission after an ACK", where + " tb " + FmtI(tv.id));
                        }
                        if (opt1 && a.nNack == 0)
                        {
                            ++retxNoNack;
                            V.Add(arm + " retransmission without a NACK", where + " tb " + FmtI(tv.id));
                        }
                    }
                    bool acked = false;
                    for (const PTx* x : tv.leader) acked = acked || x->decision == 1;
                    if (acked)
                    {
                        ++ackTbs;
                        auto nx = nextTb.find(tv.id);
                        if (nx != nextTb.end() && first[nx->second]->slot - tv.initial->slot == rriSlots)
                        {
                            ++ackKept;
                        }
                        else
                        {
                            ++ackResel;
                        }
                    }
                    auto nx = nextTb.find(tv.id);
                    if (nx != nextTb.end())
                    {
                        ++perKeptDen;
                        perKeptNum += (first[nx->second]->slot - tv.initial->slot == rriSlots) ? 1 : 0;
                    }
                }
                const long pk = static_cast<long>(r.R("pkts", 0));
                const long nt = static_cast<long>(r.R("pkts_not_tx", 0));
                pktsTot += pk;
                notTx += nt;
                newTbTot += r.NewTbsInWindow();
                ackNoRetxLeft += static_cast<long>(r.R("ack_no_retx_left", 0));
                // F20: every packet of the window got a TB
                if (std::labs(r.NewTbsInWindow() - (pk - nt)) > 3)
                {
                    ++missingNew;
                    V.Add("F20 new-data transmissions == packets (+-3)", where + " new TBs " + FmtI(r.NewTbsInWindow()) + " packets " + FmtI(pk - nt));
                }
            }
            if (runs == 0)
            {
                continue;
            }
            const std::string pre = rs + " " + arm + ": ";
            T.Check(pre + "no retransmission after an (implicit / all-)ACK", retxAfterAck == 0,
                    Opt().Exp("0 of " + FmtI(tbsTot) + " TBs").Obs(FmtI(retxAfterAck)).Tol("0").Sev("H"));
            if (arm == "R2" || arm == "R5_S1_H2")
            {
                T.Check(pre + "no leader retransmission without a NACK on the previous transmission", retxNoNack == 0,
                        Opt().Exp("0").Obs(FmtI(retxNoNack)).Tol("0").Sev("H").Ref("TS 38.321 5.22.1.3 [V]"));
            }
            T.Check(pre + "F20: every packet of the window has its ndi=1 transmission (a synthetic ACK never cancels the next period's TB)",
                    missingNew == 0,
                    Opt().Exp("new TBs == packets (+-3 per run) in every run").Obs(FmtI(missingNew) + " of " + FmtI(runs) + " runs differ; new TBs " +
                                                                                  FmtI(newTbTot) + ", packets " + FmtI(pktsTot) + ", not transmitted " + FmtI(notTx))
                        .Tol("3 per run (window boundary)").Sev("M").Fnd("F20")
                        .Note("guarded by SetSpsSafeAck: the implicit ACK is delivered only while reserved retransmissions remain (ack_no_retx_left counts the "
                              "skipped deliveries: " + FmtI(ackNoRetxLeft) + ")"));
            T.Info(pre + "ACKed TBs followed by the next new transmission exactly one period later (reservation kept)",
                   Opt().Obs(FmtI(ackKept) + " kept, " + FmtI(ackResel) + " reselected or last of the run, of " + FmtI(ackTbs) + " ACKed TBs; periods kept overall " +
                             Fmt(perKeptDen ? static_cast<double>(perKeptNum) / perKeptDen : 0.0, 4))
                       .Note("reselection (counter expiry without keep, SDU misfit) legitimately moves the grant; F01 checks the counter statistics"));
        }
    }
    const std::string art = V.Write("B06_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
