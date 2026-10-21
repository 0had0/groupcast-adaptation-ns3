// C05_option1_helper_nack (W: SYN_NEAR, SYN_MARGINAL; C: CORE, SPSDYN; R5_S1_H2, R4_S1_H2 as control): TEST_PLAN 0.3 (2): in Option 1 with a helper the named
// helper's NACK uses a dedicated resource and the leader reclaims the next handed slot exactly as in R6; every other member stays NACK-only.  R4 (Kuo baseline) never reclaims.
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
    TestReport T("C05", "option1_helper_nack", "C");
    T.Summary("R5: the named helper's NACK makes the leader reclaim the next handed slot (helper_reclaim + helper_nack_no_slot == helper_nack, leader retransmissions == reclaims, "
              "no reclaim without a helper NACK); the helper-miss events H0 / H0b of R5 are quantified; R4 never reclaims.");
    T.AddRef("TEST_PLAN 0.3 (2): --opt1HelperNack (non-3GPP: Option 1's shared NACK resource cannot identify the NACKer)");
    ViolationLog V;
    for (const std::string rs : {"SYN_NEAR", "SYN_MARGINAL", "CORE", "SPSDYN"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty())
        {
            continue;
        }
        T.AddRunset(rs);
        for (const std::string arm : {"R5_S1_H2", "R4_S1_H2"})
        {
            long runs = 0, tbs = 0, nack = 0, reclaim = 0, noSlot = 0, stale = 0, retxAttr = 0, retxNoHelperNack = 0, h0 = 0, h0b = 0, helperAcks = 0, p2Tbs = 0;
            for (const auto& rr : Select(all, arm))
            {
                Run r;
                if (!r.Load(rr) || !r.hasAtt || !r.hasHsel)
                {
                    continue;
                }
                ++runs;
                T.AddNRuns(1);
                nack += static_cast<long>(r.R("helper_nack", 0));
                reclaim += static_cast<long>(r.R("helper_reclaim", 0));
                noSlot += static_cast<long>(r.R("helper_nack_no_slot", 0));
                stale += static_cast<long>(r.R("helper_nack_stale", 0));
                helperAcks += static_cast<long>(r.R("helper_ack_rx", 0));
                const bool p2 = rr.traffic.rfind("periodic2", 0) == 0;
                for (const TbView& tv : WindowTbViews(r))
                {
                    ++tbs;
                    p2Tbs += p2 ? 1 : 0;
                    const int h = tv.helperNode;
                    if (h >= 1 && h <= kMembers && tv.initial->status[h - 1] == 0)
                    {
                        ++h0; // the helper missed the SCI of the TB: it cannot NACK (and cannot relay)
                        bool other = false;
                        for (int m = 1; m <= kMembers; ++m)
                            if (m != h && tv.initial->status[m - 1] == 2) other = true;
                        h0b += other ? 1 : 0;
                    }
                    for (size_t j = 1; j < tv.all.size(); ++j)
                    {
                        const PTx& x = *tv.all[j];
                        if (x.side != 0 || !r.InWin(tv.all[j - 1]->t))
                            continue;
                        ++retxAttr; // a leader retransmission: in R5 a reclaim, which needs the named helper's NACK on the previous transmission
                        if (h < 1 || !((tv.all[j - 1]->nackMask >> (h - 1)) & 1u))
                        {
                            ++retxNoHelperNack;
                            V.Add(arm + " leader retransmission without a NACK of the named helper", rs + "/" + rr.tag + " tb " + FmtI(tv.id) + " slot " + FmtI(x.slot));
                        }
                    }
                }
            }
            if (runs == 0)
            {
                continue;
            }
            const std::string pre = rs + " " + arm + ": ";
            if (arm == "R4_S1_H2")
            {
                T.Check(pre + "R4 (Kuo baseline) never reclaims: helper_reclaim == 0 and no leader retransmission", reclaim == 0 && retxAttr == 0, Opt().Exp("0").Obs(FmtI(reclaim) + " reclaims, " + FmtI(retxAttr) + " leader retransmissions").Tol("0").Sev("H"));
                continue;
            }
            T.Check(pre + "helper_reclaim + helper_nack_no_slot == helper_nack", reclaim + noSlot == nack, Opt().Exp(FmtI(nack)).Obs(FmtI(reclaim) + " + " + FmtI(noSlot)).Tol("0").Sev("H"));
            T.Check(pre + "leader retransmissions (attributed to the previous transmission in the window) == reclaims", retxAttr == reclaim, Opt().Exp(FmtI(reclaim)).Obs(FmtI(retxAttr)).Tol("0").Sev("H"));
            T.Check(pre + "no reclaim without a NACK of the named helper on the previous transmission", retxNoHelperNack == 0, Opt().Exp("0 of " + FmtI(retxAttr)).Obs(FmtI(retxNoHelperNack)).Tol("0").Sev("H"));
            T.Check(pre + "the helper never ACKs in Option 1 (explicit ACK received == 0)", helperAcks == 0, Opt().Exp("0").Obs(FmtI(helperAcks)).Tol("0").Sev("M"));
            if (rs != "SYN_NEAR")
                T.Check(pre + "R5 helper NACK is active: helper_nack > 0 over the run-set", nack > 0, Opt().Exp("> 0").Obs(FmtI(nack)).Sev("M").Note("TEST_PLAN 0.3: helper_reclaim, helper_nack, helper_nack_stale are non-zero for R5"));
            const Prop p0 = Wilson(h0, tbs), pb = Wilson(h0b, tbs);
            T.Info(pre + "reclaims " + FmtI(reclaim) + ", helper NACKs " + FmtI(nack) + " (stale, no reclaim: " + FmtI(stale) + "), TBs " + FmtI(tbs), Opt().Obs(FmtI(runs) + " runs; reclaims per TB " + Fmt(tbs ? static_cast<double>(reclaim) / tbs : 0.0, 4)));
            T.Info(pre + "H0 (the named helper missed the SCI, so it neither NACKs nor relays) / H0b (and another member needed the TB)",
                   Opt().Obs("H0 " + Fmt(p0.p, 4) + " [" + Fmt(p0.lo, 4) + ", " + Fmt(p0.hi, 4) + "] (" + FmtI(h0) + " of " + FmtI(tbs) + " TBs), H0b " + Fmt(pb.p, 4) + " [" + Fmt(pb.lo, 4) + ", " + Fmt(pb.hi, 4) + "] (" + FmtI(h0b) + ")")
                       .Note("no leader fallback exists for R5 either: a helper that missed the SCI is silent (G08 requirement); counted from the initial transmission's status"));
        }
    }
    const std::string art = V.Write("C05_violations");
    if (!art.empty())
        T.Artefact(art);
    return T.Finish();
}
