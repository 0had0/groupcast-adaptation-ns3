// C02_relay_gating (W: SYN_MARGINAL; C: CORE): the helper relays only when the README rule says so: R4 blind (iff it decoded),
// R5 after an overheard NACK, R6 unless every other member ACKed and nobody NACKed; never without having decoded the TB.
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"

#include <map>

using namespace ptest;

namespace
{
/// R6 success on a transmission: every non-helper member newly decoded it (and ACKed) and nobody NACKed
bool
AllAckR6(const PTx& p, int helper)
{
    if (p.nNackSent > 0)
    {
        return false;
    }
    for (int m = 1; m <= kMembers; ++m)
    {
        if (m == helper)
        {
            continue;
        }
        if (p.status[m - 1] != 1)
        {
            return false;
        }
    }
    return true;
}
} // namespace

namespace
{
struct CopyStat
{
    long runs{0}, copies{0}, wasted{0}, a{0}, b{0}, d{0}, dStatus2{0};
    double wastedSub{0};
};

/// R6: helper copies sent although every member already held the TB, classified by the transmission that preceded the copy
CopyStat
CopyAnalyse(const std::vector<RunRef>& runs, const std::string& arm)
{
    CopyStat S;
    for (const auto& rr : runs)
    {
        Run r;
        if (!r.Load(rr) || !r.hasAtt || !r.hasAlltx || !r.hasHsel)
        {
            continue;
        }
        ++S.runs;
        for (const TbView& tv : WindowTbViews(r))
        {
            for (size_t j = 1; j < tv.all.size(); ++j)
            {
                const PTx& c = *tv.all[j];
                if (c.side != 1)
                {
                    continue;
                }
                ++S.copies;
                bool allHave = true;
                for (int m = 1; m <= kMembers; ++m)
                {
                    if (!r.IsSelf(c, m))
                    {
                        allHave = allHave && r.DecodedBy(*tv.tb, static_cast<int>(j) - 1, m);
                    }
                }
                if (!allHave)
                {
                    continue;
                }
                ++S.wasted;
                S.wastedSub += c.subch();
                const PTx& prev = *tv.all[j - 1];
                bool has2 = false;
                for (int m = 1; m <= kMembers; ++m)
                {
                    if (m == tv.helperNode || r.IsSelf(prev, m))
                    {
                        continue;
                    }
                    if (j >= 2 && r.DecodedBy(*tv.tb, static_cast<int>(j) - 2, m) && prev.status[m - 1] == 2)
                    {
                        has2 = true;
                    }
                }
                if (prev.side == 0)
                {
                    (has2 ? S.a : S.b)++;
                }
                else
                {
                    ++S.d;
                    S.dStatus2 += has2 ? 1 : 0;
                }
            }
        }
    }
    (void)arm;
    return S;
}
} // namespace

int
main()
{
    TestReport T("C02", "relay_gating", "C");
    T.Summary("Relay gating follows the README: R4 relays iff it decoded; R5 only after an overheard NACK; R6 unless all other "
              "members ACKed; no relay without a decode; the number of copies per TB is reported.");
    T.AddRef("README (custom design): HelperFeedbackMode 0 blind / 1 after NACK / 2 unless all ACK; relay_without_nack, relay_without_decode, relay_after_allack");
    ViolationLog V;
    std::map<std::string, std::map<int, long>> copiesDist; // "rs/arm" -> copies -> TBs

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
        for (const std::string arm : {"R4_S1_H2", "R5_S1_H2", "R6_S1_H2"})
        {
            long runs = 0, tbs = 0, copies = 0, noDecode = 0, noNack = 0, afterAllAck = 0, afterAllAckCopy = 0, missed = 0, wasted = 0, wastedSubch = 0,
                 r4Mismatch = 0, runCsvNoDecode = 0, runCsvNoNack = 0, runCsvAfterAll = 0, tooLate = 0;
            for (const auto& rr : Select(all, arm))
            {
                Run r;
                if (!r.Load(rr) || !r.hasAtt || !r.hasAlltx || !r.hasHsel)
                {
                    continue;
                }
                ++runs;
                T.AddNRuns(1);
                runCsvNoDecode += static_cast<long>(r.R("relay_without_decode", 0));
                runCsvNoNack += static_cast<long>(r.R("relay_without_nack", 0));
                runCsvAfterAll += static_cast<long>(r.R("relay_after_allack", 0));
                tooLate += static_cast<long>(r.R("coop_too_late", 0));
                const std::string where = rs + "/" + rr.arm + "/" + rr.tag;
                for (const TbView& tv : WindowTbViews(r))
                {
                    ++tbs;
                    const int h = tv.helperNode;
                    const bool dec = (h >= 1 && h <= kMembers && tv.initial->status[h - 1] == 1);
                    copiesDist[rs + "/" + arm][static_cast<int>(tv.helper.size())]++;
                    copies += static_cast<long>(tv.helper.size());
                    // walk the TB in time order
                    for (size_t j = 1; j < tv.all.size(); ++j)
                    {
                        const PTx& c = *tv.all[j];
                        if (c.side != 1)
                        {
                            continue;
                        }
                        // the helper must hold the TB (any earlier transmission, e.g. a reclaimed leader retransmission) before it relays
                        if (!(c.node >= 1 && c.node <= kMembers && r.DecodedBy(*tv.tb, static_cast<int>(j) - 1, c.node)))
                        {
                            ++noDecode;
                            V.Add("relay_without_decode", where + " tb " + FmtI(tv.id) + " copy slot " + FmtI(c.slot));
                        }
                        const PTx& prev = *tv.all[j - 1];
                        if (arm == "R5_S1_H2" && prev.nNackSent == 0)
                        {
                            ++noNack;
                            V.Add("R5 copy without a NACK on the previous transmission", where + " tb " + FmtI(tv.id) + " copy slot " + FmtI(c.slot));
                        }
                        if (arm == "R6_S1_H2")
                        {
                            // with the F25 fix a member that already holds the TB ACKs the retransmission too (status 0 in _att): count it as an ACK
                            bool ackAll = AllAckR6(prev, h);
                            if (!ackAll && prev.nNackSent == 0 && j >= 2)
                            {
                                ackAll = true;
                                for (int m = 1; m <= kMembers; ++m)
                                {
                                    if (m == h || r.IsSelf(prev, m)) continue;
                                    ackAll = ackAll && (prev.status[m - 1] == 1 || (prev.status[m - 1] == 0 && r.DecodedBy(*tv.tb, static_cast<int>(j) - 2, m)));
                                }
                            }
                            if (ackAll && prev.side == 0 && AllAckR6(prev, h))
                            {
                                ++afterAllAck;
                                V.Add("R6 copy although every other member ACKed", where + " tb " + FmtI(tv.id) + " copy slot " + FmtI(c.slot));
                            }
                            else if (ackAll)
                            {
                                ++afterAllAckCopy; // (also counts a leader transmission whose all-ACK includes presumed re-ACKs of members that already held the TB) // after the helper's own copy: its gating only looks at the feedback to the LEADER's transmission
                            }
                            // wasted: every member already holds the TB before the copy
                            bool allHave = true;
                            for (int m = 1; m <= kMembers; ++m)
                            {
                                allHave = allHave && r.DecodedBy(*tv.tb, static_cast<int>(j) - 1, m);
                            }
                            if (allHave)
                            {
                                ++wasted;
                                wastedSubch += c.rbLen / kSubchPrbs;
                            }
                        }
                    }
                    // missed relays: gating says relay, but no copy
                    if (dec && arm != "R4_S1_H2")
                    {
                        const int nCopies = static_cast<int>(tv.helper.size());
                        auto trig = [&](const PTx& p) {
                            return arm == "R5_S1_H2" ? p.nNackSent > 0 : !AllAckR6(p, h);
                        };
                        if (nCopies == 0 && trig(*tv.initial))
                        {
                            ++missed;
                        }
                        else if (nCopies == 1 && trig(*tv.helper[0]) && r.maxNumTx > 2)
                        {
                            ++missed;
                        }
                    }
                    if (arm == "R4_S1_H2")
                    {
                        const int want = dec ? r.maxNumTx - 1 : 0;
                        r4Mismatch += (static_cast<int>(tv.helper.size()) != want) ? 1 : 0;
                    }
                }
            }
            if (runs == 0)
            {
                continue;
            }
            const std::string pre = rs + " " + arm + ": ";
            T.Check(pre + "relay_without_decode recomputed == 0", noDecode == 0,
                    Opt().Exp("0 of " + FmtI(copies) + " copies").Obs(FmtI(noDecode)).Tol("0").Sev("H"));
            T.Check(pre + "run.csv relay_without_decode == 0", runCsvNoDecode == 0, Opt().Exp("0").Obs(FmtI(runCsvNoDecode)).Tol("0").Sev("M"));
            if (arm == "R4_S1_H2")
            {
                T.Check(pre + "copies per TB == maxNumTx-1 iff the helper decoded the initial transmission", r4Mismatch == 0,
                        Opt().Exp("0 of " + FmtI(tbs) + " TBs").Obs(FmtI(r4Mismatch) + " (coop_too_late events in the runs: " + FmtI(tooLate) + ")")
                            .Tol("0").Sev("H").Fnd("F18"));
            }
            if (arm == "R5_S1_H2")
            {
                T.Check(pre + "relay_without_nack recomputed == 0 (every copy follows a NACK sent on the previous transmission of the TB)", noNack == 0,
                        Opt().Exp("0 of " + FmtI(copies) + " copies").Obs(FmtI(noNack)).Tol("0").Sev("H"));
                T.Check(pre + "run.csv relay_without_nack == 0", runCsvNoNack == 0, Opt().Exp("0").Obs(FmtI(runCsvNoNack)).Tol("0").Sev("M"));
                T.Info(pre + "TBs where the gating asks for a relay but no copy was sent (helper decoded, NACK seen)",
                       Opt().Obs(FmtI(missed) + " of " + FmtI(tbs) + " TBs; coop_too_late " + FmtI(tooLate)).Fnd("F18"));
            }
            if (arm == "R6_S1_H2")
            {
                T.Check(pre + "relay_after_allack recomputed == 0 (no copy once every other member ACKed and nobody NACKed)", afterAllAck == 0,
                        Opt().Exp("0 of " + FmtI(copies) + " copies").Obs(FmtI(afterAllAck)).Tol("0").Sev("H").Fnd("F34").Note("F34 (new, minor): a copy is sent although the previous leader transmission drew only ACKs and no NACK (all 7 other members). The helper cancels only after overhearing HelperGroupSize = 7 ACKs (nr-sl-ue-mac.cc ~1699), so it must have missed one ACK that the leader received; no half-duplex or NACK is visible in the logs (events: CORE R6 hwy_ivd30_periodic1tb_none_s3 tb 175, rbt_ivd30_periodic2fx_moderate_s1 tbs 2392, 7443, 7536; 4 of 94147 copies); mechanism not isolated (needs a diagnostic trace of the helper PSFCH reception)"));
                T.Info(pre + "run.csv relay_after_allack (a copy although the previous transmission drew only ACKs) vs copies after the helper's own previous copy",
                       Opt().Obs("run.csv " + FmtI(runCsvAfterAll) + "; recount after a leader transmission " + FmtI(afterAllAck) + ", after the helper's own copy (all ACK incl. re-ACKs) " + FmtI(afterAllAckCopy))
                           .Note("by design: the helper cancels a relay slot only on the ACKs overheard for the LEADER's transmission (nr-sl-ue-mac.cc ~1699, feedback on its own copy is swallowed ~2858), so a copy after a copy that completed the group is sent; the run.csv counter counts those. The 3 events in CORE R6 rbt_ivd30_periodic2fx_moderate_s1 and 1 in hwy_ivd30_periodic1tb_none_s3 are of this kind"));
                {
                    const CopyStat fx = CopyAnalyse(Select(all, arm), arm);
                    const CopyStat f0 = CopyAnalyse(Select(LoadManifest(rs, OutDirV1()), arm), arm);
                    T.Check(pre + "F25 (fixed): every copy sent after all members already hold the TB is explained: (a) a member that must ACK has SCI-2 failed (status 2) or (d) the previous transmission is the helper's own copy (its feedback is not evaluated by design); (b) leader transmission with no status-2 member = unexplained",
                            fx.b == 0,
                            Opt().Fnd(fx.b > 0 ? "F34" : "F25").Sev("M").Exp("(b) = 0")
                                .Obs(FmtI(fx.wasted) + " of " + FmtI(fx.copies) + " copies (" + Fmt(fx.copies ? 100.0 * fx.wasted / fx.copies : 0.0, 3) + " %, " + Fmt(fx.wastedSub, 6) + " subchannel-slots) were sent when all members already held the TB: (a) status 2 after a leader transmission "
                                     + FmtI(fx.a) + ", (d) after the helper's own copy " + FmtI(fx.d) + " (of which a status-2 member " + FmtI(fx.dStatus2) + "), (b) unexplained " + FmtI(fx.b))
                                .Tol("0")
                                .Note("relay gating: nr-sl-ue-mac.cc ~1699 cancels a relay slot only when the ACKs overheard for the LEADER's transmission (m_txRnti == relayAwaitRnti, lines 2805-2812) reach HelperGroupSize; feedback on the helper's own copy "
                                      "is swallowed (m_relayFbKeys, ~2858), so a second copy after a first copy that completed the group is sent by design.  With the F25 fix members re-ACK a retransmission of the leader, so the only leader-side residual is a member whose SCI-2 failed"));
                    if (f0.copies > 0)
                    {
                        T.Info(pre + "copies sent after every member held the TB, before the fix (reference tests/out_prefix_v1)",
                               Opt().Fnd("F25").Obs(FmtI(f0.wasted) + " of " + FmtI(f0.copies) + " copies (" + Fmt(100.0 * f0.wasted / f0.copies, 3) + " %), " + Fmt(f0.wastedSub, 6) + " subchannel-slots; after a leader transmission " + FmtI(f0.a + f0.b)
                                                    + " (status 2: " + FmtI(f0.a) + "), after a helper copy " + FmtI(f0.d)));
                    }
                }
                T.Info(pre + "TBs where the gating asks for a relay but no copy was sent (helper decoded)",
                       Opt().Obs(FmtI(missed) + " of " + FmtI(tbs) + " TBs; coop_too_late " + FmtI(tooLate)).Fnd("F18"));
            }
            std::string d;
            for (const auto& [k, n] : copiesDist[rs + "/" + arm])
            {
                d += std::to_string(k) + " copies: " + FmtI(n) + " TBs; ";
            }
            T.Info(pre + "copies per TB", Opt().Obs(d));
        }
    }
    {
        const std::string rel = "results/C02_copies_per_tb.csv";
        CsvWriter w(TestsRoot() + "/" + rel, "runset_arm,copies,tbs");
        for (const auto& [k, h] : copiesDist)
        {
            for (const auto& [c, n] : h)
            {
                w.Row({k, std::to_string(c), std::to_string(n)});
            }
        }
        T.Artefact(rel);
    }
    const std::string art = V.Write("C02_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
