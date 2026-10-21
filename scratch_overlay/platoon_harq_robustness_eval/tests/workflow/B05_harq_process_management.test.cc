// B05_harq_process_management (W: SYN_MARGINAL; C: CORE): HARQ ids in 0..15, no two TBs active on the same id, transmissions
// per TB <= maxNumTx, stale decisions (F08) and the chosenOfHarq overwrite risk (F05).
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
    TestReport T("B05", "harq_process_management", "B");
    T.Summary("HARQ processes are used consistently: ids 0..15, one active TB per id, at most maxNumTx transmissions per TB; "
              "stale decisions (HARQ id reused before the decision) and the per-HARQ-id helper choice overwrite are measured.");
    T.AddRef("TS 38.321 5.22.1.3 (one HARQ process per TB), TS 38.212 8.4.1.1 (4-bit HARQ process number) [V]");
    T.AddRef("TEST_PLAN F05 / F08");
    ViolationLog V;

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
        std::map<std::string, std::vector<double>> staleRate; // arm -> per-run stale/decisions
        std::map<std::string, std::pair<double, double>> fbCnt; // arm -> (fb_late, helper_nack_stale) of run.csv (F30 fix counters)
        std::map<std::string, long> fbRuns;
        long gTxBadP2 = 0, gTbsP2 = 0, gOverlapP2 = 0, decP2 = 0, staleP2 = 0, decOther = 0, staleOther = 0;
        long gRuns = 0, gIdBad = 0, gTxBad = 0, gOverlap = 0, gTbs = 0, gF05 = 0, gF05Tbs = 0, gHselTbs = 0;
        for (const auto& rr : all)
        {
            if (rr.runset != rs)
            {
                continue;
            }
            Run r;
            if (!r.Load(rr) || !r.hasAtt)
            {
                continue;
            }
            ++gRuns;
            T.AddNRuns(1);
            const std::string where = rs + "/" + rr.arm + "/" + rr.tag;
            for (const PTx& x : r.txs)
            {
                if (x.harq < 0 || x.harq > 15)
                {
                    ++gIdBad;
                    V.Add("HARQ id in 0..15", where + " slot " + FmtI(x.slot) + " id " + std::to_string(x.harq));
                }
            }
            // per-TB budget and per-HARQ-id non-overlap of leader TBs
            std::map<int, std::vector<std::pair<double, double>>> byId; // harq -> [(t_first, t_last)] by TB order
            for (const auto& [id, tb] : r.tbs)
            {
                ++gTbs;
                gTbsP2 += (rr.traffic.rfind("periodic2", 0) == 0) ? 1 : 0;
                if (static_cast<int>(tb.tx.size()) > r.maxNumTx)
                {
                    ++(rr.traffic.rfind("periodic2", 0) == 0 ? gTxBadP2 : gTxBad);
                    V.Add("transmissions per TB <= maxNumTx", where + " tb " + FmtI(id) + " tx " + std::to_string(tb.tx.size()));
                }
                double tf = 1e18, tl = -1;
                int harq = -1;
                for (int u : tb.tx)
                {
                    const PTx& x = r.txs[u];
                    if (x.side == 0)
                    {
                        tf = std::min(tf, x.t);
                        tl = std::max(tl, x.t);
                        harq = x.harq;
                    }
                }
                if (harq >= 0)
                {
                    byId[harq].push_back({tf, tl});
                }
            }
            for (auto& [h, v] : byId)
            {
                std::sort(v.begin(), v.end());
                for (size_t i = 1; i < v.size(); ++i)
                {
                    if (v[i].first <= v[i - 1].second)
                    {
                        ++(rr.traffic.rfind("periodic2", 0) == 0 ? gOverlapP2 : gOverlap);
                        V.Add("two TBs active on the same HARQ id", where + " id " + std::to_string(h) + " t " + Fmt(v[i].first, 7));
                    }
                }
            }
            fbCnt[rr.arm].first += r.R("fb_late", 0);
            fbCnt[rr.arm].second += r.R("helper_nack_stale", 0);
            ++fbRuns[rr.arm];
            // stale decisions
            long dec = 0, stale = 0;
            for (const PTx& x : r.txs)
            {
                if (x.side == 0 && r.InWin(x.t))
                {
                    ++dec;
                    stale += (x.decision == 2) ? 1 : 0;
                    if (rr.traffic.rfind("periodic2", 0) == 0) { ++decP2; staleP2 += (x.decision == 2) ? 1 : 0; }
                    else { decOther += 1; staleOther += (x.decision == 2) ? 1 : 0; }
                }
            }
            staleRate[rr.arm].push_back(dec ? static_cast<double>(stale) / dec : 0.0);
            // F05: a later _hsel row on the same HARQ id (selection at publish) before the TB's last copy
            if (r.hasHsel)
            {
                const auto& td = r.hselT.D("t_decide");
                const auto& tn = r.hselT.D("t_ndi");
                const auto& hid = r.hselT.D("harq_id");
                for (const TbView& tv : WindowTbViews(r))
                {
                    ++gHselTbs;
                    double tlast = 0;
                    for (const PTx* x : tv.all) tlast = std::max(tlast, x->t);
                    bool overwritten = false;
                    for (size_t i = 0; i < r.hselT.nrows; ++i)
                    {
                        if (static_cast<int>(hid[i]) == tv.initial->harq && tn[i] > tv.initial->t + 1e-9 && td[i] > tv.initial->t && td[i] <= tlast)
                        {
                            overwritten = true;
                            break;
                        }
                    }
                    gF05Tbs += overwritten ? 1 : 0;
                    if (overwritten) V.Add("F05 chosenOfHarq overwritten before the last copy", where + " tb " + FmtI(tv.id));
                }
            }
        }
        if (gRuns == 0)
        {
            T.Skip(rs + "/data", "no runs with _att");
            continue;
        }
        const std::string pre = rs + ": ";
        T.Check(pre + "HARQ id in 0..15 for every transmission", gIdBad == 0, Opt().Exp("0").Obs(FmtI(gIdBad)).Tol("0").Sev("M").Ref("TS 38.212 8.4.1.1 [V]"));
        T.Check(pre + "transmissions per TB (leader + helper copies) <= maxNumTx, periodic1tb/aperiodic1tb", gTxBad == 0,
                Opt().Exp("0 of " + FmtI(gTbs - gTbsP2) + " TBs").Obs(FmtI(gTxBad)).Tol("0").Sev("H"));
        T.Check(pre + "no two leader TBs active on the same HARQ id (a new ndi=1 only after the previous TB's last transmission), periodic1tb/aperiodic1tb", gOverlap == 0,
                Opt().Exp("0").Obs(FmtI(gOverlap)).Tol("0").Sev("H"));
        if (gTbsP2 > 0)
        {
            T.Check(pre + "periodic2tb: transmissions per TB (leader + helper copies) <= maxNumTx", gTxBadP2 == 0,
                    Opt().Fnd("F30").Sev("M").Exp("0 of " + FmtI(gTbsP2) + " TBs").Obs(FmtI(gTxBadP2) + " TBs with more than maxNumTx transmissions").Tol("0")
                        .Note("F30 (new): periodic2tb with R6.  TB k and TB k+1 share a HARQ id (RRI 10 ms = 20 slots < the HARQ round trip of TB k).  The helper NACK of the LAST transmission of TB k (a stale decision, decision 2) arrives after TB k+1 started and is bound to TB k+1 (no NDI / slot check, DV4 / F29): the leader reclaims TB k+1's first handed slot (tx_in_tb 2) although the helper decoded TB k+1 and relays in the same slot.  Result: two identical PSSCH (same HARQ id, RV 2, same PRBs) in one slot, a physical self-collision without background traffic and a 4th transmission of TB k+1.  All 12 events of SYN_MARGINAL (R6, 3 seeds) show the helper NACK between the previous TB's last transmission and the event, a stale previous decision and leader tx_in_tb 2 (checked on _alltx, _ltx and _nack)"));
            T.Check(pre + "periodic2tb: no two leader TBs active on the same HARQ id (a new ndi=1 only after the previous TB's last transmission)", gOverlapP2 == 0,
                    Opt().Exp("0").Obs(FmtI(gOverlapP2)).Tol("0").Sev("M").Fnd("F08"));
            T.CheckXfail(pre + "periodic2tb: stale decisions == 0 (the HARQ id is not reused before the decision)", staleP2 == 0,
                         Opt().Fnd("F08").Sev("M").Exp("0 of " + FmtI(decP2) + " leader transmissions").Obs(FmtI(staleP2) + " stale decisions (" + Fmt(decP2 ? 100.0 * staleP2 / decP2 : 0.0, 3) + " %); periodic1tb/aperiodic1tb: " + FmtI(staleOther) + " of " + FmtI(decOther))
                             .Note("a stale decision is a leader transmission whose HARQ id was reused by the next TB before the decision (feedback dropped); it is the precondition of F30 (new): periodic2tb with R6.  TB k and TB k+1 share a HARQ id (RRI 10 ms = 20 slots < the HARQ round trip of TB k).  The helper NACK of the LAST transmission of TB k (a stale decision, decision 2) arrives after TB k+1 started and is bound to TB k+1 (no NDI / slot check, DV4 / F29): the leader reclaims TB k+1's first handed slot (tx_in_tb 2) although the helper decoded TB k+1 and relays in the same slot.  Result: two identical PSSCH (same HARQ id, RV 2, same PRBs) in one slot, a physical self-collision without background traffic and a 4th transmission of TB k+1.  All 12 events of SYN_MARGINAL (R6, 3 seeds) show the helper NACK between the previous TB's last transmission and the event, a stale previous decision and leader tx_in_tb 2 (checked on _alltx, _ltx and _nack)"));
        }
        {
            std::string d;
            for (const auto& [arm, c] : fbCnt) d += arm + ": fb_late " + FmtI(static_cast<long>(c.first)) + ", helper_nack_stale " + FmtI(static_cast<long>(c.second)) + " (" + FmtI(fbRuns[arm]) + " runs); ";
            T.Info(pre + "F30 fix counters of run.csv: PSFCH outside every occasion (fb_late) and helper NACKs for a TB its HARQ id no longer carries (helper_nack_stale, no reclaim)", Opt().Fnd("F30").Obs(d)
                       .Note("TEST_PLAN 0.2: --psfchExactAttribution binds feedback only to the pending transmission whose decision is due within the next slot"));
        }
        for (const auto& [arm, v] : staleRate)
        {
            const MeanCI m = MeanCi(v);
            T.Info(pre + arm + ": stale decision rate (decision 2: HARQ id reused before the decision, feedback dropped)",
                   Opt().Obs(m.ok() ? FmtCi(m.mean, m.lo, m.hi, 4) : Fmt(m.mean, 4)).Fnd("F08").Note("per run: stale decisions / leader transmissions in the KPI window"));
            double mx = 0;
            for (double x : v) mx = std::max(mx, x);
            T.Metric(rs + "_" + arm + "_stale_rate_mean", m.mean);
            T.Metric(rs + "_" + arm + "_stale_rate_max", mx);
        }
        if (gHselTbs > 0)
        {
            T.CheckXfail(pre + "F05: the per-HARQ-id helper choice is not overwritten before the TB's last copy", gF05Tbs == 0,
                         Opt().Fnd("F05").Sev("M").Exp("0 overwritten TBs")
                             .Obs(FmtI(gF05Tbs) + " of " + FmtI(gHselTbs) + " TBs had a later selection on the same HARQ id before their last copy")
                             .Note("chosenOfHarq is keyed by HARQ id; geometry of side 1 uses the static helper node (main.cc:330, 566-576, 2159)"));
        }
        else
        {
            T.Info(pre + "F05 not testable", Opt().Obs("no arm with _hsel in this run-set").Fnd("F05"));
        }
    }
    const std::string art = V.Write("B05_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
