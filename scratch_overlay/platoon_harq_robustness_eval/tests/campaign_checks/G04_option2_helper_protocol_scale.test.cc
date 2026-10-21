// G04_option2_helper_protocol_scale (C: CORE, R6_S1_H2 vs R3): the C03 invariants of the silent-helper protocol at campaign scale, the blind-spot
// rate and its cost to the helper, the stale-decision rate (F08), the PSFCH saving of the silent helper and the NACK-only reclaim trigger.
#include "common/analysis_kit.h"
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <cmath>
#include <map>

using namespace ptest;

int
main()
{
    TestReport T("G04", "option2_helper_protocol_scale", "G");
    T.Summary("R6 at scale: the helper never ACKs, the leader reacts only to the helper's NACK (reclaim) and to non-helper feedback, no false ACK; "
              "blind-spot, stale-decision and PSFCH-count statistics against the helper-less Option 2 (R3).");
    T.AddRef("README silent helper; TS 38.321 5.22.1.3 [V]; TEST_PLAN F07, F08");
    ViolationLog V;
    for (const std::string rs : {"CORE", "SPSDYN"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { T.Skip(rs + " data", rs + " manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        long runs = 0, ackRx = 0, suppExp = 0, suppRun = 0, reclaim = 0, noSlot = 0, hNack = 0, falseAck = 0, leaderRetx = 0, reclaimWithoutHelperNack = 0;
        long namedTbs = 0, h0 = 0, h0a = 0, h0b = 0, stale = 0, leaderTx = 0;
        std::vector<double> hPrr, otherMean, hPrrR3, psfchR6, psfchR3;
        std::map<std::string, std::vector<double>> r3Prr; // cell|seed -> helper member prr in R3
        for (const auto& rr : Select(all, "R3"))
        {
            Run r;
            if (!r.Load(rr)) continue;
            double tb = std::max(1.0, r.NewTbsInWindow() * 1.0);
            psfchR3.push_back((r.R("nack_rx") + r.R("explicit_ack_rx")) / tb);
        }
        for (const auto& rr : Select(all, "R6_S1_H2"))
        {
            Run r;
            if (!r.Load(rr) || !r.hasAtt || !r.hasAlltx || !r.hasHsel) continue;
            ++runs;
            T.AddNRuns(1);
            ackRx += static_cast<long>(r.R("helper_ack_rx", 0));
            suppRun += static_cast<long>(r.R("helper_ack_suppressed", 0));
            reclaim += static_cast<long>(r.R("helper_reclaim", 0));
            noSlot += static_cast<long>(r.R("helper_nack_no_slot", 0));
            hNack += static_cast<long>(r.R("helper_nack", 0));
            const std::string where = rs + "/" + rr.tag;
            const double tbs = std::max(1.0, r.NewTbsInWindow() * 1.0);
            psfchR6.push_back((r.R("nack_rx") + r.R("explicit_ack_rx")) / tbs);
            std::map<int64_t, int> helperOfTb;
            for (const PTx& x : r.txs) if (x.side == 0 && x.txInTb == 1) helperOfTb[x.tb] = r.HelperOf(x);
            for (const PTx& x : r.txs)
            {
                if (x.side != 0) continue;
                auto it = helperOfTb.find(x.tb);
                if (it == helperOfTb.end()) continue;
                const int h = it->second;
                if (h >= 1 && h <= kMembers && x.status[h - 1] == 1) ++suppExp;
                if (r.InWin(x.t))
                {
                    ++leaderTx;
                    stale += (x.decision == 2);
                    leaderRetx += (x.txInTb > 1);
                }
            }
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
                        for (int m = 1; m <= kMembers; ++m) if (m != h) ok = ok && r.DecodedBy(*tv.tb, static_cast<int>(k), m);
                        if (!ok) { ++falseAck; V.Add("false ACK", where + " tb " + FmtI(tv.id)); }
                    }
                    // every leader retransmission follows a NACK of the named helper on the previous transmission
                    if (x.side == 0 && x.txInTb > 1 && k > 0)
                    {
                        const PTx& prev = *tv.all[k - 1];
                        const bool hn = h >= 1 && ((prev.nackMask >> (h - 1)) & 1);
                        if (!hn) { ++reclaimWithoutHelperNack; V.Add("leader retransmission without a helper NACK", where + " tb " + FmtI(tv.id) + " slot " + FmtI(x.slot)); }
                    }
                }
                if (h >= 1 && h <= kMembers && tv.initial->status[h - 1] == 0)
                {
                    ++h0;
                    bool succ = false;
                    for (const PTx* x : tv.leader) succ = succ || x->decision == 1;
                    bool nonHelperNeed = false;
                    for (int m = 1; m <= kMembers; ++m) if (m != h && tv.initial->status[m - 1] != 1) nonHelperNeed = true;
                    h0a += (!nonHelperNeed && succ);
                    h0b += nonHelperNeed;
                }
            }
            // PRR cost to the helper
            std::map<int, int> cnt;
            for (const auto& [tb, h] : helperOfTb) cnt[h]++;
            int hm = 0, best = 0;
            for (const auto& [h, n] : cnt) if (n > best) { best = n; hm = h; }
            if (hm >= 1 && hm <= kMembers)
            {
                hPrr.push_back(r.MemberCol("prr_pdb", hm));
                double s = 0; int n = 0;
                for (int m = 1; m <= kMembers; ++m) if (m != hm) { s += r.MemberCol("prr_pdb", m); ++n; }
                otherMean.push_back(s / n);
            }
        }
        if (runs == 0) continue;
        const std::string pre = rs + " R6: ";
        T.Check(pre + "helper_ack_rx == 0 at scale", ackRx == 0, Opt().Exp("0").Obs(FmtI(ackRx)).Tol("0").Sev("H"));
        T.Check(pre + "helper_ack_suppressed == recount of helper decodes on its TBs", suppRun == suppExp, Opt().Exp(FmtI(suppExp)).Obs(FmtI(suppRun)).Tol("0").Sev("H"));
        T.Check(pre + "helper_reclaim + helper_nack_no_slot == helper NACKs", reclaim + noSlot == hNack, Opt().Exp(FmtI(hNack)).Obs(FmtI(reclaim) + " + " + FmtI(noSlot)).Tol("0").Sev("H"));
        T.Check(pre + "no false ACK (decision 1 only when every non-helper member holds the TB)", falseAck == 0, Opt().Exp("0 of " + FmtI(leaderTx)).Obs(FmtI(falseAck)).Tol("0").Sev("H").Fnd("F31").Note("F31 (new, exposed by the F25 fix): a member whose SCI-2A of the new TB failed keeps the previous TB prev-decoded flag on that HARQ id and ACKs the retransmission without holding the TB (see B03)"));
        T.Check(pre + "every leader retransmission (reclaim) follows a NACK of the named helper; the leader never reacts to an ACK", reclaimWithoutHelperNack == 0,
                Opt().Exp("0 of " + FmtI(leaderRetx)).Obs(FmtI(reclaimWithoutHelperNack)).Tol("0").Sev("H"));
        const Prop b0 = Wilson(h0, namedTbs), ba = Wilson(h0a, namedTbs), bb = Wilson(h0b, namedTbs);
        T.Info(pre + "blind spot: helper missed the SCI (H0) / declared successful (H0a) / a non-helper member needed a relay (H0b), per TB",
               Opt().Fnd("F07").Obs("H0 " + FmtCi(b0.p, b0.lo, b0.hi, 4) + ", H0a " + FmtCi(ba.p, ba.lo, ba.hi, 4) + ", H0b " + FmtCi(bb.p, bb.lo, bb.hi, 4) + " of " + FmtI(namedTbs) + " TBs (details: G08)"));
        const MeanCI mh = MeanCi(hPrr), mo = MeanCi(otherMean);
        T.Info(pre + "PRR_pdb of the named helper vs the mean of the other members", Opt().Fnd("F07").Obs("helper " + (mh.ok() ? FmtCi(mh.mean, mh.lo, mh.hi, 4) : Fmt(mh.mean, 4)) + ", others " + (mo.ok() ? FmtCi(mo.mean, mo.lo, mo.hi, 4) : Fmt(mo.mean, 4))));
        const Prop st = Wilson(stale, leaderTx);
        T.Info(pre + "stale decisions (HARQ id reused before the decision, F08), per leader transmission", Opt().Fnd("F08").Obs(FmtCi(st.p, st.lo, st.hi, 5) + " (" + FmtI(stale) + " of " + FmtI(leaderTx) + ")"));
        if (!psfchR3.empty() && !psfchR6.empty())
        {
            T.Info(pre + "PSFCH messages per TB (ACK + NACK received by the transmitter), R6 vs R3", Opt().Obs("R6 " + Fmt(Mean(psfchR6), 4) + ", R3 " + Fmt(Mean(psfchR3), 4) + " (saving of the silent helper and of the shorter TBs: " + Fmt(Mean(psfchR3) - Mean(psfchR6), 4) + " per TB)")
                       .Note("R3 uses all maxNumTx transmissions in most TBs (F25) while R6 stops earlier, so the difference is not only the one missing ACK"));
        }
        T.Info(pre + "retransmissions triggered by the helper's NACK (leader reclaims) per TB", Opt().Obs(FmtI(reclaim) + " reclaims over " + FmtI(namedTbs) + " TBs = " + Fmt(namedTbs ? static_cast<double>(reclaim) / namedTbs : 0.0, 4)));
    }
    const std::string art = V.Write("G04_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
