// C01_helper_signalling (W: SYN_NEAR, SYN_MARGINAL; C: CORE): the helper named per TB in the SCI-2A (_hsel.applied at the TB's
// ndi=1 time) is the node that sends every relayed copy of that TB; copies keep the TB's HARQ id and rb_len, carry ndi=0, never
// share a slot with a transmission of the leader, and use distinct slots (README: S1 leaderRes, per-TB selection).
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"

#include <map>
#include <set>

using namespace ptest;

int
main()
{
    TestReport T("C01", "helper_signalling", "C");
    T.Summary("Every relayed copy comes from the helper named for its TB, with the TB's HARQ id and size, ndi 0, in a slot the "
              "leader does not use.");
    T.AddRef("README (custom design): SCI-2A CoopInfo.helperId, S1 leaderRes, relay_by_unchosen invariant");
    ViolationLog V;

    for (const std::string rs : {"SYN_NEAR", "SYN_MARGINAL", "CORE"})
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
        for (const std::string arm : {"R4_S1_H2", "R5_S1_H2", "R6_S1_H2"})
        {
            long runs = 0, tbs = 0, copies = 0, unchosen = 0, unknownNode = 0, harqBad = 0, rbBad = 0, ndiBad = 0, sameSlotTb = 0,
                 leaderClash = 0, hselMissing = 0, singleSlotTbs = 0, gAll = 0, gLess = 0, gSingle = 0, runCsvUnchosen = 0, rvTwo = 0, sameSlotP2 = 0, clashP2 = 0, tbsP2 = 0;
            std::map<int, long> helperDist;
            for (const auto& rr : Select(all, arm))
            {
                Run r;
                if (!r.Load(rr) || !r.hasAtt || !r.hasAlltx || !r.hasHsel)
                {
                    continue;
                }
                ++runs;
                T.AddNRuns(1);
                r.GrantSlotStats(gAll, gLess, gSingle);
                runCsvUnchosen += static_cast<long>(r.R("relay_by_unchosen", 0));
                std::set<int64_t> leaderSlots;
                for (const PTx& x : r.txs)
                {
                    if (x.side == 0)
                    {
                        leaderSlots.insert(x.slot);
                    }
                }
                const std::string where = rs + "/" + rr.arm + "/" + rr.tag;
                for (const TbView& tv : WindowTbViews(r))
                {
                    ++tbs;
                    tbsP2 += (rr.traffic.rfind("periodic2", 0) == 0) ? 1 : 0;
                    // hsel row at the ndi=1 time
                    bool have = false;
                    const auto& tn = r.hselT.D("t_ndi");
                    for (size_t i = 0; i < r.hselT.nrows && !have; ++i)
                    {
                        have = std::fabs(tn[i] - tv.initial->t) < 0.5 * kSlotS;
                    }
                    if (!have && r.GrantSlotsAt(tv.initial->t) == 1)
                    {
                        ++singleSlotTbs; // a single-slot SPS grant hands no slot: the selector is not run, no row by design
                    }
                    else if (!have)
                    {
                        ++hselMissing;
                        V.Add("each ndi=1 TB has a _hsel row", where + " tb " + FmtI(tv.id));
                    }
                    helperDist[tv.helperNode]++;
                    std::set<int64_t> slots;
                    for (const PTx* x : tv.all)
                    {
                        if (!slots.insert(x->slot).second)
                        {
                            ++(rr.traffic.rfind("periodic2", 0) == 0 ? sameSlotP2 : sameSlotTb);
                            V.Add("no two platoon PSSCH of one TB in a slot", where + " tb " + FmtI(tv.id));
                        }
                    }
                    for (const PTx* c : tv.helper)
                    {
                        ++copies;
                        if (c->node < 0)
                        {
                            ++unknownNode;
                        }
                        else if (c->node != tv.helperNode)
                        {
                            ++unchosen;
                            V.Add("relay_by_unchosen", where + " tb " + FmtI(tv.id) + " node " + std::to_string(c->node) + " applied " + std::to_string(tv.helperNode));
                        }
                        harqBad += (c->harq != tv.initial->harq) ? 1 : 0;
                        rbBad += (c->rbLen != tv.initial->rbLen) ? 1 : 0;
                        ndiBad += (c->ndi != 0) ? 1 : 0;
                        rvTwo += (c->rv == 2) ? 1 : 0;
                        if (leaderSlots.count(c->slot))
                        {
                            ++(rr.traffic.rfind("periodic2", 0) == 0 ? clashP2 : leaderClash);
                            V.Add("leader never transmits in the slot of its TB's copy (S1 usurp)", where + " tb " + FmtI(tv.id) + " slot " + FmtI(c->slot));
                        }
                    }
                }
            }
            if (runs == 0)
            {
                continue;
            }
            const std::string pre = rs + " " + arm + ": ";
            T.Check(pre + "every ndi=1 TB sent under a grant with a handed slot has a helper-selection row at its transmission time", hselMissing == 0,
                    Opt().Exp("0 of " + FmtI(tbs)).Obs(FmtI(hselMissing) + " without a row; " + FmtI(singleSlotTbs) + " TBs under single-slot SPS grants (no handed slot, selector not run)").Tol("0").Sev("M"));
            T.Check(pre + "SPS grants reserve maxNumTx slots (as the blind arms do)", gLess == 0,
                    Opt().Fnd("F33").Sev("H").Exp("0 of " + FmtI(gAll) + " grants with fewer than maxNumTx slots")
                        .Obs(FmtI(gLess) + " of " + FmtI(gAll) + " SPS grants (" + Fmt(gAll ? 100.0 * gLess / gAll : 0.0, 3) + " %) reserve fewer than maxNumTx slots, " + FmtI(gSingle) + " a single slot (no retransmission opportunity at all)")
                        .Tol("0")
                        .Note("F33 (new): the blind arms (R1, R4) always get 3 reserved slots; the feedback arms (R5, R6, and R2/R3) must keep K + PSFCH processing spacing between slots inside the (PDB-limited, --t2Ms=9 for periodic2fx) selection window "
                              "and under full-band periodic2fx TBs they obtain only 2 slots (about 55 % of the grants in background cells) or 1 slot (3-6 %); fewer retransmission opportunities than R4 for the same maxNumTx; reference: CORE hwy/rbt periodic2fx, 100 % 3 slots for R1/R4"));

            T.Check(pre + "relay_by_unchosen recomputed == 0 (every copy comes from the applied helper)", unchosen == 0 && unknownNode == 0,
                    Opt().Exp("0 of " + FmtI(copies) + " copies").Obs(FmtI(unchosen) + " by another node, " + FmtI(unknownNode) + " with unknown node").Tol("0").Sev("H"));
            T.Check(pre + "run.csv relay_by_unchosen == 0", runCsvUnchosen == 0, Opt().Exp("0").Obs(FmtI(runCsvUnchosen)).Tol("0").Sev("M"));
            T.Check(pre + "copies keep the TB's HARQ id and rb_len and carry ndi 0", harqBad == 0 && rbBad == 0 && ndiBad == 0,
                    Opt().Exp("0").Obs(FmtI(harqBad) + " HARQ id / " + FmtI(rbBad) + " rb_len / " + FmtI(ndiBad) + " ndi violations").Tol("0").Sev("H"));
            T.Check(pre + "no two platoon PSSCH of one TB in the same slot, periodic1tb/aperiodic1tb", sameSlotTb == 0, Opt().Exp("0").Obs(FmtI(sameSlotTb)).Tol("0").Sev("H"));
            T.Check(pre + "the leader never transmits in a slot where its TB's copy is sent (S1 usurp), periodic1tb/aperiodic1tb", leaderClash == 0,
                    Opt().Exp("0").Obs(FmtI(leaderClash)).Tol("0").Sev("H"));
            if (tbsP2 > 0)
            {
                T.Check(pre + "periodic2tb: no leader / helper PSSCH in one slot for the same TB", sameSlotP2 == 0 && clashP2 == 0,
                        Opt().Fnd("F30").Sev("M").Exp("0")
                            .Obs(FmtI(sameSlotP2) + " TBs with two platoon PSSCH in one slot, " + FmtI(clashP2) + " leader/copy slot clashes (of " + FmtI(tbsP2) + " periodic2tb TBs)")
                            .Tol("0").Note("F30 (new): periodic2tb with R6.  TB k and TB k+1 share a HARQ id (RRI 10 ms = 20 slots < the HARQ round trip of TB k).  The helper NACK of the LAST transmission of TB k (a stale decision, decision 2) arrives after TB k+1 started and is bound to TB k+1 (no NDI / slot check, DV4 / F29): the leader reclaims TB k+1's first handed slot (tx_in_tb 2) although the helper decoded TB k+1 and relays in the same slot.  Result: two identical PSSCH (same HARQ id, RV 2, same PRBs) in one slot, a physical self-collision without background traffic and a 4th transmission of TB k+1.  All 12 events of SYN_MARGINAL (R6, 3 seeds) show the helper NACK between the previous TB's last transmission and the event, a stale previous decision and leader tx_in_tb 2 (checked on _alltx, _ltx and _nack)"));
            }
            std::string d;
            for (const auto& [m, n] : helperDist)
            {
                d += "member " + std::to_string(m) + ": " + FmtI(n) + " TBs; ";
            }
            T.Info(pre + "helper chosen per TB (Kuo, applied)", Opt().Obs(d));
            T.Info(pre + "RV of relayed copies", Opt().Obs(FmtI(rvTwo) + " of " + FmtI(copies) + " copies carry RV 2")
                                                      .Note("the blind leader (R1) cycles RV 0,2,3,1; helper copies always use RV 2 (the first retransmission RV), "
                                                            "so the second copy repeats the first one's redundancy version (chase-like) instead of RV 3"));
        }
    }
    const std::string art = V.Write("C01_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
