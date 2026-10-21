// A04_patch_gating (W: SYN_NEAR, SYN_MARGINAL, SYN_HM): the patched blind-retransmission path (HARQ-disabled logical channel
// sends maxNumTx copies), feedback disabled in R1, R1 vs R2 slot format, helperMode off vs none.
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <dirent.h>

#include <fstream>
#include <map>
#include <set>
#include <sstream>

using namespace ptest;

int
main()
{
    TestReport T("A04", "patch_gating", "A");
    T.Summary("R1 depends on an ungated patch change of the NR scheduler (blind copies for HARQ-disabled LCs): it delivers "
              "exactly maxNumTx copies per TB with feedback disabled; R1/R2 share the slot format; the scheduler class "
              "(helperMode off vs none) does not change the leader-only PRR.");
    T.AddRef("TEST_PLAN F11/DV9 (ungated patch change at nr-sl-ue-mac-scheduler-fixed-mcs.cc 'isBlind')");
    T.AddRef("TS 38.321 5.22.1.1 (blind retransmission) [V]");
    ViolationLog V;

    // ---- 1. R1: exactly maxNumTx copies, same HARQ id and rb_len ---------------------------------------------------------
    for (const std::string rs : {"SYN_NEAR", "SYN_MARGINAL"})
    {
        T.AddRunset(rs);
        long tbs = 0, ok = 0, short_ = 0, shortPdb = 0, noFeedback = 0, runs1 = 0;
        long nackRows = 0, nackRx = 0, implicitAck = 0, explicitAck = 0, decisionNonZero = 0;
        std::map<std::string, std::map<int, long>> dist; // traffic -> tx count -> TBs
        for (const auto& rr : Select(LoadManifest(rs), "R1"))
        {
            Run r;
            if (!r.Load(rr))
            {
                continue;
            }
            ++runs1;
            T.AddNRuns(1);
            nackRows += static_cast<long>(r.nackT.nrows);
            nackRx += static_cast<long>(r.R("nack_rx", 0));
            implicitAck += static_cast<long>(r.R("implicit_ack", 0));
            explicitAck += static_cast<long>(r.R("explicit_ack_rx", 0));
            for (const PTx& x : r.txs)
            {
                if (x.side == 0 && x.decision == 1)
                {
                    ++decisionNonZero;
                }
            }
            for (const TbView& tv : WindowTbViews(r))
            {
                ++tbs;
                const int n = static_cast<int>(tv.leader.size());
                dist[rr.traffic][n]++;
                bool same = true;
                for (const PTx* x : tv.leader)
                {
                    same = same && x->harq == tv.initial->harq && x->rbLen == tv.initial->rbLen;
                }
                if (n == r.maxNumTx && same)
                {
                    ++ok;
                }
                else
                {
                    ++short_;
                    // explained by the PDB: the selection window of periodic2tb (10 ms) cannot hold maxNumTx slots in some cases
                    if (rr.traffic.rfind("periodic2", 0) == 0 && same && n < r.maxNumTx)
                    {
                        ++shortPdb;
                    }
                    else
                    {
                        V.Add("R1 exactly maxNumTx copies, same HARQ id and rb_len",
                              rs + "/" + rr.tag + " tb " + FmtI(tv.id) + " copies " + std::to_string(n));
                    }
                }
            }
        }
        if (runs1 == 0)
        {
            T.Skip(rs + "/R1 data", "no R1 runs");
            continue;
        }
        std::string d;
        for (const auto& [traf, h] : dist)
        {
            d += traf + " {";
            for (const auto& [k, nn] : h)
            {
                d += std::to_string(k) + " tx: " + FmtI(nn) + ", ";
            }
            d += "}; ";
        }
        T.Check(rs + "/R1: every leader TB has exactly maxNumTx copies with the same HARQ id and rb_len (PDB-limited periodic2tb excepted)",
                tbs > 0 && (short_ - shortPdb) == 0,
                Opt().Exp("all " + FmtI(tbs) + " TBs").Obs(FmtI(ok) + " exact; " + FmtI(short_) + " short, of which PDB-limited periodic2tb " + FmtI(shortPdb) + " | " + d)
                    .Tol("0").Sev("H").Fnd("F11"));
        if (shortPdb > 0)
        {
            T.Info(rs + "/R1: periodic2tb TBs with fewer than maxNumTx copies (PDB 10 ms = 20 slots)",
                   Opt().Obs(FmtI(shortPdb) + " TBs").Fnd("F23").Note("recorded, not a failure (selection window shorter than the reservation pattern)"));
        }
        T.Check(rs + "/R1: feedback disabled: _nack.csv empty, nack_rx = implicit_ack = explicit_ack_rx = 0, no decision 1 (ACK)",
                nackRows == 0 && nackRx == 0 && implicitAck == 0 && explicitAck == 0 && decisionNonZero == 0,
                Opt().Exp("0 / 0 / 0 / 0 / 0").Obs(FmtI(nackRows) + " / " + FmtI(nackRx) + " / " + FmtI(implicitAck) + " / " +
                                                   FmtI(explicitAck) + " / " + FmtI(decisionNonZero))
                    .Tol("0").Sev("H").Ref("TS 38.321 5.22.1.1 [V]"));
        (void)noFeedback;
    }

    // ---- 2. R1 vs R2 slot format on SYN_NEAR ---------------------------------------------------------------------------
    {
        const auto near = LoadManifest("SYN_NEAR");
        long pairs = 0, same = 0;
        std::string firstDiff;
        for (const auto& a : Select(near, "R1"))
        {
            for (const auto& b : Select(near, "R2", a.traffic))
            {
                if (b.seed != a.seed)
                {
                    continue;
                }
                Run ra, rb;
                if (!ra.Load(a) || !rb.Load(b))
                {
                    continue;
                }
                ++pairs;
                std::map<int, long> ha, hb;
                for (const TbView& tv : WindowTbViews(ra)) ha[tv.initial->rbLen]++;
                for (const TbView& tv : WindowTbViews(rb)) hb[tv.initial->rbLen]++;
                // same traffic and seed: the packet sizes are identical, so are TB sizes and subchannel counts
                // configuration constants exactly; rb_len histogram with +-2 TBs per bin (window-boundary packet)
                bool histEq = true;
                std::set<int> keys;
                for (const auto& kv : ha) keys.insert(kv.first);
                for (const auto& kv : hb) keys.insert(kv.first);
                for (int k : keys)
                {
                    const long ca = ha.count(k) ? ha[k] : 0, cb = hb.count(k) ? hb[k] : 0;
                    histEq = histEq && std::labs(ca - cb) <= 2;
                }
                const bool eq = ra.R("tb_max_b") == rb.R("tb_max_b") && ra.R("payload_max_b") == rb.R("payload_max_b") &&
                                ra.R("n_subch") == rb.R("n_subch") && histEq;
                same += eq;
                if (!eq && firstDiff.empty())
                {
                    firstDiff = a.tag;
                }
            }
        }
        if (pairs == 0)
        {
            T.Skip("R1 vs R2 slot format", "no SYN_NEAR R1/R2 pairs");
        }
        else
        {
            T.Check("SYN_NEAR: R1 and R2 have identical tb_max_b, payload_max_b, n_subch and rb_len distribution (same slot format, F27)",
                    same == pairs,
                    Opt().Exp(FmtI(pairs) + " pairs identical").Obs(FmtI(same) + " identical" + (firstDiff.empty() ? "" : "; first difference " + firstDiff))
                        .Tol("rb_len histogram +-2 TBs per bin (window boundary), constants exact").Sev("L").Fnd("F27"));
        }
    }

    // ---- 3. helperMode off vs none --------------------------------------------------------------------------------------
    {
        T.AddRunset("SYN_HM");
        const auto hm = LoadManifest("SYN_HM");
        const auto base = LoadManifest("SYN_MARGINAL");
        std::vector<double> a, b;
        std::string cells;
        for (const auto& h : hm)
        {
            for (const auto& s : Select(base, "R2", h.traffic))
            {
                if (s.seed != h.seed || !s.leaderSps)
                {
                    continue;
                }
                Run rh, rs2;
                if (!rh.Load(h) || !rs2.Load(s))
                {
                    continue;
                }
                a.push_back(rs2.R("prr_mean"));
                b.push_back(rh.R("prr_mean"));
            }
        }
        if (a.size() < 2)
        {
            T.Skip("helperMode off vs none", "SYN_HM runs missing (campaign/run_matrix.py --runset SYN_HM)");
        }
        else
        {
            const DiffCI d = Paired(a, b);
            T.Info("R2 (helperMode=off) vs R2 + helperMode=none: paired PRR difference (SYN_MARGINAL, " + FmtI(static_cast<long>(a.size())) + " pairs)",
                   Opt().Obs("off - none = " + FmtCi(d.diff, d.lo, d.hi, 4) + (d.IncludesZero() ? " (CI includes 0)" : " (CI excludes 0)"))
                       .Note("the two arms use different scheduler classes (NrSlUeMacSchedulerFixedMcs vs NrSlCoopScheduler with "
                             "cooperation off) and different RNG streams; the comparison is statistical, not per TB"));
            T.AddNRuns(static_cast<long>(hm.size()));
        }
    }

    // ---- 4. F11 / DV9: the ungated patch --------------------------------------------------------------------------------
    {
        const std::string f = RepoRoot() + "/ns-3-dev/contrib/nr/model/nr-sl-ue-mac-scheduler-fixed-mcs.cc";
        std::ifstream in(f);
        std::string line, found;
        int n = 0;
        while (std::getline(in, line))
        {
            ++n;
            if (line.find("const bool isBlind") != std::string::npos)
            {
                found = "line " + std::to_string(n) + ": " + line.substr(line.find_first_not_of(' '));
            }
        }
        int rc = 0;
        const std::string diff = Shell("git -C '" + RepoRoot() + "/ns-3-dev/contrib/nr' diff -U0 model/nr-sl-ue-mac-scheduler-fixed-mcs.cc | grep -c 'isBlind'", &rc);
        if (found.empty())
        {
            T.Pass("F11/no ungated blind-copy patch found in the scheduler", Opt().Fnd("F11").Obs("isBlind not found"));
        }
        else
        {
            T.Xfail("F11/blind copies for HARQ-disabled LCs come from an ungated patch change",
                    Opt().Fnd("F11").Sev("M")
                        .Exp("stock 5G-LENA sends 1 copy for a HARQ-disabled LC; blind only when HARQ enabled and psfchPeriod == 0")
                        .Obs(found + "; git diff mentions isBlind on " + diff + " lines")
                        .Note("DV9: the condition '!harqEnabled && maxTx > 1' is added without an attribute gate, so every HARQ-disabled "
                              "logical channel of this build sends maxNumTx copies. R1 (and the blind arm of the study) rely on it; the stock "
                              "behaviour is shown by A03 (sl-multi-lc-dyn-gcast-no-harq reflog)."));
        }
    }
    const std::string art = V.Write("A04_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    // ---- provenance of the fixes (TEST_PLAN 0.2): every fresh run was made by the fixed simulator with both fixes on ---------------------------
    {
        long runs = 0, noCol = 0, off = 0, noCol2 = 0, wrong03 = 0;
        int nManifests = 0;
        std::string ex;
        DIR* d = opendir(OutDir().c_str());
        if (d)
        {
            while (dirent* e = readdir(d))
            {
                const std::string nm = e->d_name;
                if (nm == "." || nm == ".." || nm == "traces" || nm == "logs" || nm == "micro" || nm == "A03b")
                {
                    continue;
                }
                const auto rs = LoadManifest(nm);
                if (rs.empty())
                {
                    continue;
                }
                ++nManifests;
                for (const auto& rr : rs)
                {
                    Table t;
                    if (!LoadTable(ResolveCsv(rr.prefix + "_run.csv"), t) || t.nrows == 0)
                    {
                        continue;
                    }
                    ++runs;
                    if (!t.Has("reack_decoded") || !t.Has("psfch_exact_attr"))
                    {
                        ++noCol;
                        if (ex.size() < 200) ex += nm + "/" + rr.tag + " (no provenance columns); ";
                    }
                    else if (t.At("reack_decoded", 0) != 1 || t.At("psfch_exact_attr", 0) != 1)
                    {
                        ++off;
                        if (ex.size() < 200) ex += nm + "/" + rr.tag + " (flag off); ";
                    }
                    else if (!t.Has("blind_soft_combining") || !t.Has("opt1_helper_nack") || !t.Has("leader_fixed_size"))
                    {
                        ++noCol2;
                        if (ex.size() < 200) ex += nm + "/" + rr.tag + " (no TEST_PLAN 0.3 columns); ";
                    }
                    else
                    {
                        const double wantSize = rr.traffic == "periodic1fx" ? 300 : (rr.traffic == "periodic2fx" || rr.traffic == "aperiodic1fx") ? 1200 : 0;
                        const double wantH = (rr.arm.rfind("R5", 0) == 0) ? 1 : 0; // the helper NACK fallback exists only in Option 1 with a helper
                        if (t.At("blind_soft_combining", 0) != 0 || t.At("opt1_helper_nack", 0) != wantH || t.At("leader_fixed_size", 0) != wantSize)
                        {
                            ++wrong03;
                            if (ex.size() < 200) ex += nm + "/" + rr.tag + " (0.3 flags: soft " + Fmt(t.At("blind_soft_combining", 0), 3) + ", opt1HelperNack " + Fmt(t.At("opt1_helper_nack", 0), 3) + ", fixed size " + Fmt(t.At("leader_fixed_size", 0), 4) + "); ";
                        }
                    }
                }
            }
            closedir(d);
        }
        if (runs == 0)
        {
            T.Skip("provenance: fixed simulator", "no run-set found yet");
        }
        else
        {
            T.Check("provenance: every run of tests/out has reack_decoded = 1 and psfch_exact_attr = 1 in _run.csv (made by the fixed simulator, F25 / F30 fixes on)", noCol == 0 && off == 0 && noCol2 == 0 && wrong03 == 0,
                    Opt().Exp("0 of " + FmtI(runs) + " runs without").Obs(FmtI(noCol + noCol2) + " without the columns, " + FmtI(off) + " with a fix off, " + FmtI(wrong03) + " with wrong TEST_PLAN 0.3 flags (blind_soft_combining 0, opt1_helper_nack 1, leader_fixed_size = 300 / 1200 / 1200 B for periodic1fx / periodic2fx / aperiodic1fx else 0, opt1_helper_nack = 1 only for R5)" + (ex.empty() ? "" : ": " + ex) + " (" + FmtI(nManifests) + " manifests)")
                        .Tol("0").Sev("H").Fnd("F25")
                        .Note("TEST_PLAN 0.2: --reAckDecoded and --psfchExactAttribution default to true; the pre-fix reference is tests/out_prefix_v1 (read-only)"));
        }
    }
    return T.Finish();
}
