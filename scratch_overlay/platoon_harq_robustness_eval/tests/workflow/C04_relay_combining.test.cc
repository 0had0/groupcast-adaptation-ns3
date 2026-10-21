// C04_relay_combining (W: SYN_MARGINAL): a member that failed the leader's initial transmission and decodes a helper copy is
// counted once at the application level; the leader never counts a relayed copy as its own transmission; soft combining shows.
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <cmath>
#include <map>
#include <set>

using namespace ptest;

int
main()
{
    TestReport T("C04", "relay_combining", "C");
    T.Summary("Relayed copies are soft-combined at the members and delivered once; the leader's logs never contain a relayed copy; "
              "P(success on the copy | failed initial) exceeds P(success on a fresh initial at the same SINR).");
    T.AddRef("README (custom design): relayed copy keeps the leader's HARQ id; members soft-combine it (NrEesmIrT1); the leader ignores copies of its own TB");
    T.AddRunset("SYN_MARGINAL");
    ViolationLog V;

    const auto all = LoadManifest("SYN_MARGINAL");
    for (const std::string arm : {"R4_S1_H2", "R5_S1_H2", "R6_S1_H2"})
    {
        long copyInLtxP2 = 0, ltxVsAlltxP2 = 0;
        long runs = 0, dupRows = 0, rxGtSent = 0, rows = 0, ltxVsAlltx = 0, copyInLtx = 0, failedThenCopyOk = 0, failedThenCopy = 0;
        std::map<int, std::pair<long, long>> freshBin;  // SINR bin -> (ok, total) for fresh initial transmissions (leader, first attempt)
        std::map<int, std::pair<long, long>> combBin;   // SINR bin -> (ok, total) for first copy after a failed initial
        for (const auto& rr : Select(all, arm))
        {
            Run r;
            if (!r.Load(rr) || !r.hasAtt || !r.hasAlltx)
            {
                continue;
            }
            ++runs;
            T.AddNRuns(1);
            // app level: one row per (seq, member); rx counts <= sent
            for (int m = 0; m < kMembers; ++m)
            {
                std::set<int64_t> seen;
                long rx = 0;
                for (const auto& p : r.pkts[m])
                {
                    ++rows;
                    if (!seen.insert(p.seq).second)
                    {
                        ++dupRows;
                    }
                    rx += p.rx ? 1 : 0;
                }
                if (rx > static_cast<long>(r.pkts[m].size()))
                {
                    ++rxGtSent;
                }
            }
            // ltx never holds a relayed copy: row count == leader PSSCH of alltx (whole run); copies' slots not in ltx
            long leaderAll = 0;
            {
                const auto& node = r.alltxT.D("node");
                const auto& dst = r.alltxT.D("dst");
                const auto& x = r.alltxT.D("x");
                const auto& y = r.alltxT.D("y");
                for (size_t i = 0; i < r.alltxT.nrows; ++i)
                {
                    leaderAll += (node[i] == 0 && dst[i] == 254 && std::hypot(x[i], y[i]) < 5e4) ? 1 : 0;
                }
            }
            const bool p2 = (rr.traffic.rfind("periodic2", 0) == 0);
            (p2 ? ltxVsAlltxP2 : ltxVsAlltx) += (leaderAll != static_cast<long>(r.ltxT.nrows)) ? 1 : 0;
            std::set<int64_t> lslots;
            for (const PTx& x : r.txs) if (x.side == 0) lslots.insert(x.slot);
            for (const PTx& x : r.txs) if (x.side == 1 && lslots.count(x.slot)) ++(p2 ? copyInLtxP2 : copyInLtx);
            // soft-combining evidence
            for (const TbView& tv : WindowTbViews(r))
            {
                for (int m = 1; m <= kMembers; ++m)
                {
                    const PTx& i0 = *tv.initial;
                    if (i0.status[m - 1] == 1 || i0.status[m - 1] == 2)
                    {
                        auto& b = freshBin[static_cast<int>(std::floor(i0.sinr[m - 1] / 2.0))];
                        b.second++;
                        b.first += (i0.status[m - 1] == 1);
                    }
                    if (i0.status[m - 1] == 2 && m != tv.helperNode && !tv.helper.empty())
                    {
                        const PTx& c = *tv.helper[0];
                        if (c.status[m - 1] == 1 || c.status[m - 1] == 2)
                        {
                            ++failedThenCopy;
                            failedThenCopyOk += (c.status[m - 1] == 1);
                            auto& b = combBin[static_cast<int>(std::floor(c.sinr[m - 1] / 2.0))];
                            b.second++;
                            b.first += (c.status[m - 1] == 1);
                        }
                    }
                }
            }
        }
        if (runs == 0)
        {
            continue;
        }
        const std::string pre = "SYN_MARGINAL " + arm + ": ";
        T.Check(pre + "one app-level row per (packet, member): no duplicate deliveries", dupRows == 0,
                Opt().Exp("0 duplicates in " + FmtI(rows) + " rows").Obs(FmtI(dupRows)).Tol("0").Sev("H"));
        T.Check(pre + "sum rx <= sent for every member", rxGtSent == 0, Opt().Exp("0").Obs(FmtI(rxGtSent)).Tol("0").Sev("H"));
        T.Check(pre + "_ltx.csv holds exactly the leader's PSSCH (no relayed copy counted as a leader transmission)", ltxVsAlltx == 0 && copyInLtx == 0,
                Opt().Exp("rows == leader PSSCH in _alltx; no copy slot in _ltx").Obs(FmtI(ltxVsAlltx) + " runs differ, " + FmtI(copyInLtx) + " copy slots found in _ltx")
                    .Tol("0").Sev("H"));
        if (copyInLtxP2 > 0 || ltxVsAlltxP2 > 0)
        {
            T.Check(pre + "periodic2tb: no copy slot coincides with a leader transmission", copyInLtxP2 == 0 && ltxVsAlltxP2 == 0,
                    Opt().Fnd("F30").Sev("M").Exp("0").Obs(FmtI(copyInLtxP2) + " copy slots coincide with a leader PSSCH, " + FmtI(ltxVsAlltxP2) + " runs with ltx != alltx").Tol("0")
                        .Note("F30 (new): periodic2tb with R6.  TB k and TB k+1 share a HARQ id (RRI 10 ms = 20 slots < the HARQ round trip of TB k).  The helper NACK of the LAST transmission of TB k (a stale decision, decision 2) arrives after TB k+1 started and is bound to TB k+1 (no NDI / slot check, DV4 / F29): the leader reclaims TB k+1's first handed slot (tx_in_tb 2) although the helper decoded TB k+1 and relays in the same slot.  Result: two identical PSSCH (same HARQ id, RV 2, same PRBs) in one slot, a physical self-collision without background traffic and a 4th transmission of TB k+1.  All 12 events of SYN_MARGINAL (R6, 3 seeds) show the helper NACK between the previous TB's last transmission and the event, a stale previous decision and leader tx_in_tb 2 (checked on _alltx, _ltx and _nack)"));
        }
        // weighted comparison over the bins the combined samples occupy
        double expFresh = 0, nComb = 0, okComb = 0;
        for (const auto& [bin, v] : combBin)
        {
            auto it = freshBin.find(bin);
            if (it == freshBin.end() || it->second.second < 10)
            {
                continue;
            }
            expFresh += static_cast<double>(v.second) * it->second.first / it->second.second;
            nComb += v.second;
            okComb += v.first;
        }
        if (nComb >= 20)
        {
            const Prop pc = Wilson(static_cast<long>(okComb), static_cast<long>(nComb));
            if (arm == "R4_S1_H2")
            {
                // TEST_PLAN 0.3 (1): blind copies are independent attempts (--blindSoftCombining false): the copy's success must not depend on the failed initial
                const double fresh = expFresh / nComb;
                T.Info(pre + "blind copies: P(success on the first copy | failed initial) vs P(success on a fresh initial at the same SINR bin)",
                       Opt().Obs(FmtCi(pc.p, pc.lo, pc.hi, 4) + " (n=" + FmtI(static_cast<long>(nComb)) + ") vs fresh " + Fmt(fresh, 4) + (pc.lo <= fresh && fresh <= pc.hi ? " (inside the CI)" : " (outside the CI)"))
                           .Note("Slowly varying correlated fading makes the outcomes of successive copies dependent even without combining, so this comparison cannot isolate soft combining; the absence of combining is established by the provenance check (blind_soft_combining = 0 in every run, A04). Per-copy SINR bin only; the combined SINR is not logged."));
            }
            T.Info(pre + "soft combining: P(success on the first copy | failed initial) vs P(success on a fresh initial at the same SINR bin)",
                   Opt().Obs("copy after failed initial " + FmtCi(pc.p, pc.lo, pc.hi, 4) + " (n=" + FmtI(static_cast<long>(nComb)) + "), fresh initial at the same SINR " +
                             Fmt(expFresh / nComb, 4) + (pc.lo > expFresh / nComb ? " -> combining gain visible" : " -> no clear combining gain"))
                       .Note("SINR of the copy alone, 2 dB bins; directional evidence only (section 7.3)"));
            T.Metric(arm + "_comb_p", pc.p);
            T.Metric(arm + "_fresh_p_same_sinr", expFresh / nComb);
        }
        else
        {
            T.Info(pre + "soft combining evidence", Opt().Obs("only " + FmtI(static_cast<long>(nComb)) + " comparable samples").Note("not enough failed-then-copied pairs"));
        }
        (void)failedThenCopy;
        (void)failedThenCopyOk;
    }
    const std::string art = V.Write("C04_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
