// B01_blind_workflow (W: SYN_NEAR, SYN_MARGINAL; R1, R4_S1_H2): blind retransmission (HARQ feedback disabled) follows
// TS 38.214 8.1.4 / TS 38.321 5.22.1.1: all copies are sent whatever the reception outcome, same HARQ process, RV order
// 0,2,3,1, NDI 1,0,0, inside the PDB; R4: the leader sends once and the helper relays in the handed-over slots iff it decoded.
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
    TestReport T("B01", "blind_workflow", "B");
    T.Summary("R1 sends every copy regardless of reception (NDI 1,0,0, RV 0,2,3,1, same HARQ id, inside the PDB); in R4 the "
              "leader transmits once and the helper (Kuo choice) sends exactly maxNumTx-1 copies iff it decoded the initial "
              "transmission, in the slots the leader handed over.");
    T.AddRef("TS 38.214 8.1.4, TS 38.321 5.22.1.1 (blind retransmission, HARQ feedback disabled) [V]");
    T.AddRef("TS 38.212 8.3.1.1 (SCI 1-A: N_MAX resources inside a 32-slot window) [V]; README S1 (leader hands slots to the helper)");
    ViolationLog V;
    const int rvSeq[4] = {0, 2, 3, 1};

    for (const std::string rs : {"SYN_NEAR", "SYN_MARGINAL"})
    {
        T.AddRunset(rs);
        const auto runs = LoadManifest(rs);
        long r1Tbs = 0, r1Ndi = 0, r1Rv = 0, r1Distinct = 0, r1Pdb = 0, r1PdbN = 0, r1PdbSlot = 0, r1PdbP1 = 0, r1PdbP1Late = 0, r1MaxLate = 0, r1Win32 = 0, r1Harq = 0, r1Rb = 0;
        std::map<int, std::pair<long, long>> freshBin, copyBin; // SINR bin (2 dB) -> (ok, total): fresh initials; second blind copy after a failed initial
        long r1AllDecAtFirst = 0, r1AllDecAtFirstWith3 = 0, nRuns1 = 0;
        std::map<int, long> r1CountDist;
        long r4Tbs = 0, r4Leader1 = 0, r4Iff = 0, r4HelperDec = 0, r4Dec0 = 0, r4Dec1 = 0, r4DecFull = 0, r4NotDecCopies = 0,
             r4Disjoint = 0, r4Win = 0, r4Rb = 0, nRuns4 = 0;
        for (const auto& rr : runs)
        {
            const bool isR1 = rr.arm == "R1", isR4 = rr.arm == "R4_S1_H2";
            if (!isR1 && !isR4)
            {
                continue;
            }
            Run r;
            std::string err;
            if (!r.Load(rr, &err) || !r.hasAtt)
            {
                T.Fail(rs + "/load " + rr.arm + "/" + rr.tag, Opt().Obs(err.empty() ? "no _att.csv" : err));
                continue;
            }
            T.AddNRuns(1);
            (isR1 ? nRuns1 : nRuns4)++;
            const std::string where = rs + "/" + rr.arm + "/" + rr.tag;
            std::set<int64_t> leaderSlots;
            for (const PTx& x : r.txs)
            {
                if (x.side == 0)
                {
                    leaderSlots.insert(x.slot);
                }
            }
            for (const TbView& tv : WindowTbViews(r))
            {
                if (isR1)
                {
                    ++r1Tbs;
                    for (int m = 1; m <= kMembers; ++m)
                    {
                        const PTx& i0 = *tv.initial;
                        if (i0.status[m - 1] == 1 || i0.status[m - 1] == 2)
                        {
                            auto& b = freshBin[static_cast<int>(std::floor(i0.sinr[m - 1] / 2.0))];
                            ++b.second;
                            b.first += (i0.status[m - 1] == 1);
                        }
                        if (i0.status[m - 1] == 2 && tv.leader.size() > 1 && (tv.leader[1]->status[m - 1] == 1 || tv.leader[1]->status[m - 1] == 2))
                        {
                            auto& b = copyBin[static_cast<int>(std::floor(tv.leader[1]->sinr[m - 1] / 2.0))];
                            ++b.second;
                            b.first += (tv.leader[1]->status[m - 1] == 1);
                        }
                    }
                    r1CountDist[static_cast<int>(tv.leader.size())]++;
                    bool ndiOk = true, rvOk = true, harqOk = true, rbOk = true, win32 = true, pdbOk = true;
                    std::set<int64_t> slots;
                    for (size_t k = 0; k < tv.leader.size(); ++k)
                    {
                        const PTx& x = *tv.leader[k];
                        ndiOk = ndiOk && (x.ndi == (k == 0 ? 1 : 0));
                        rvOk = rvOk && (x.rv == rvSeq[k % 4]);
                        harqOk = harqOk && x.harq == tv.initial->harq;
                        rbOk = rbOk && x.rbLen == tv.initial->rbLen;
                        win32 = win32 && (x.slot - tv.initial->slot <= 31);
                        slots.insert(x.slot);
                        if (!std::isnan(tv.tGen))
                        {
                            ++r1PdbN;
                            const bool in = x.t >= tv.tGen - 1e-9 && x.t <= tv.tGen + r.pdbS + 1e-9;
                            pdbOk = pdbOk && in;
                            r1Pdb += in ? 1 : 0;
                            r1PdbSlot += (x.t >= tv.tGen - 1e-9 && x.t <= tv.tGen + r.pdbS + 0.5 * kSlotS * 2 + 1e-9) ? 1 : 0;
                            if (rr.traffic.rfind("periodic2", 0) == 0) { ++r1PdbP1; r1PdbP1Late += in ? 0 : 1; }
                            r1MaxLate = std::max<long>(r1MaxLate, static_cast<long>(std::llround(std::max(0.0, x.t - tv.tGen - r.pdbS) * 1e6)));
                        }
                    }
                    r1Ndi += ndiOk;
                    r1Rv += rvOk;
                    r1Harq += harqOk;
                    r1Rb += rbOk;
                    r1Win32 += win32;
                    r1Distinct += (slots.size() == tv.leader.size());
                    if (!ndiOk) V.Add("R1 NDI sequence 1,0,0", where + " tb " + FmtI(tv.id));
                    if (!rvOk) V.Add("R1 RV sequence 0,2,3,1", where + " tb " + FmtI(tv.id));
                    if (!win32) V.Add("R1 copies within 32 slots", where + " tb " + FmtI(tv.id));
                    if (!pdbOk) V.Add("R1 copies inside PDB", where + " tb " + FmtI(tv.id));
                    // blind definition: copies are sent although everybody decoded the first one
                    bool allDec = true;
                    for (int m = 1; m <= kMembers; ++m)
                    {
                        allDec = allDec && tv.initial->status[m - 1] == 1;
                    }
                    if (allDec)
                    {
                        ++r1AllDecAtFirst;
                        r1AllDecAtFirstWith3 += (static_cast<int>(tv.leader.size()) == r.maxNumTx) ? 1 : 0;
                    }
                }
                else
                {
                    ++r4Tbs;
                    r4Leader1 += (tv.leader.size() == 1) ? 1 : 0;
                    if (tv.leader.size() != 1) V.Add("R4 leader transmits once", where + " tb " + FmtI(tv.id));
                    const int h = tv.helperNode;
                    const bool dec = (h >= 1 && h <= kMembers && tv.initial->status[h - 1] == 1);
                    const int want = dec ? r.maxNumTx - 1 : 0;
                    const int got = static_cast<int>(tv.helper.size());
                    r4HelperDec += dec;
                    if (got == want)
                    {
                        ++r4Iff;
                    }
                    else
                    {
                        V.Add("R4 copies == (helper decoded ? maxNumTx-1 : 0)",
                              where + " tb " + FmtI(tv.id) + " helper " + std::to_string(h) + " decoded " + std::to_string(dec) +
                                  " copies " + std::to_string(got));
                    }
                    if (dec)
                    {
                        (got == 0 ? r4Dec0 : got == 1 ? r4Dec1 : r4DecFull)++;
                    }
                    else
                    {
                        r4NotDecCopies += got;
                    }
                    bool disjoint = true, win = true, rb = true;
                    for (const PTx* c : tv.helper)
                    {
                        disjoint = disjoint && !leaderSlots.count(c->slot);
                        win = win && (c->slot - tv.initial->slot <= 31) && c->slot > tv.initial->slot;
                        rb = rb && c->rbLen == tv.initial->rbLen;
                    }
                    r4Disjoint += disjoint;
                    r4Win += win;
                    r4Rb += rb;
                    if (!disjoint) V.Add("R4 copies in slots without a leader PSSCH", where + " tb " + FmtI(tv.id));
                }
            }
        }
        T.AddNRuns(0);
        if (nRuns1 == 0 && nRuns4 == 0)
        {
            T.Skip(rs + "/data", "no R1 / R4_S1_H2 runs in the manifest");
            continue;
        }
        auto eq = [&](const std::string& name, long ok, long n, const std::string& sev = "H") {
            T.Check(name, n > 0 && ok == n, Opt().Exp(FmtI(n) + " of " + FmtI(n)).Obs(FmtI(ok) + " of " + FmtI(n)).Tol("0").Sev(sev));
        };
        if (nRuns1)
        {
            eq(rs + "/R1: NDI sequence 1,0,0", r1Ndi, r1Tbs);
            eq(rs + "/R1: RV sequence 0,2,3,1", r1Rv, r1Tbs);
            eq(rs + "/R1: every copy keeps the HARQ process id", r1Harq, r1Tbs);
            eq(rs + "/R1: every copy has the initial rb_len", r1Rb, r1Tbs);
            eq(rs + "/R1: copies in distinct slots", r1Distinct, r1Tbs);
            eq(rs + "/R1: all copies within 32 slots of the first (SCI-1A N_MAX window, DV8)", r1Win32, r1Tbs, "M");
            if (r1PdbN > 0)
            {
                T.Check(rs + "/R1: every copy inside [t_gen, t_gen + PDB]", r1Pdb == r1PdbN,
                             Opt().Fnd("F23").Sev("L").Exp(FmtI(r1PdbN) + " copies").Obs(FmtI(r1Pdb) + " inside; " + FmtI(r1PdbN - r1Pdb) + " late, all periodic2tb (" + FmtI(r1PdbP1Late) + " of " + FmtI(r1PdbP1) + " periodic2tb copies), max " + Fmt(r1MaxLate / 1e3, 3) + " ms late").Tol("0")
                                 .Note("T2 is clipped to the PDB (nr-sl-ue-mac.cc:416-433: T2 = min(PDB, T2 attribute)), and the PDB reaches the LC (groupcast_flows -> LC info -> GetLcPdb in the scheduler, fixed-mcs.cc:970 / nr_sl_coop_scheduler.cc:1012), but the FULL PDB (20 slots) is passed, counted from the scheduling slot n (the first slot boundary after the packet was generated), not the remaining PDB (PDB - (n - t_gen)); TS 38.214 8.1.4 requires T2 <= remaining PDB.  The last copy therefore lands at n + 20 = 20.6-20.8 slots after generation, 0.3-0.4 ms (one slot) late. --t2Ms would not change this (the attribute 33 slots > PDB 20 slots, min() already picks the PDB); a correct fix passes the remaining PDB per packet"));
                T.Check(rs + "/R1: every copy inside [t_gen, t_gen + PDB + 1 slot]", r1PdbSlot == r1PdbN,
                        Opt().Exp(FmtI(r1PdbN)).Obs(FmtI(r1PdbSlot)).Tol("1 slot = 0.5 ms").Sev("M").Fnd("F23")
                            .Note("the overshoot is bounded by one slot"));
                if (rs == "SYN_NEAR")
                    T.Info("SYN_NEAR and SYN_MARGINAL R1 counts coincide by construction", Opt().Obs("the blind arm's leader transmissions do not depend on reception or geometry (no background): _ltx.csv of the same traffic and seed is byte-identical in both run-sets")
                               .Note("checked with cmp for periodic1tb, aperiodic1tb (seeds 1-3) and periodic2tb (seeds 1, 3); the two run-sets are not independent samples for R1 timing checks"));
            }
            {
                double expFresh = 0, nC = 0, okC = 0;
                for (const auto& [bin, v] : copyBin)
                {
                    auto it = freshBin.find(bin);
                    if (it == freshBin.end() || it->second.second < 10) continue;
                    expFresh += static_cast<double>(v.second) * it->second.first / it->second.second;
                    nC += v.second;
                    okC += v.first;
                }
                if (nC >= 30)
                {
                    const Prop pc = Wilson(static_cast<long>(okC), static_cast<long>(nC));
                    const double fresh = expFresh / nC;
                    T.Info(rs + "/R1: P(success of the 2nd copy | failed initial) vs P(success of a fresh initial at the same SINR bin)",
                           Opt().Obs(FmtCi(pc.p, pc.lo, pc.hi, 4) + " (n=" + FmtI(static_cast<long>(nC)) + ") vs fresh " + Fmt(fresh, 4) + (pc.lo <= fresh && fresh <= pc.hi ? " (inside the CI)" : " (outside the CI)"))
                               .Note("Slowly varying correlated fading makes the outcomes of successive copies dependent even without combining, so this comparison cannot isolate soft combining; the absence of combining is established by the provenance check (blind_soft_combining = 0 in every run, A04). Per-copy SINR bin only; the combined SINR is not logged."));
                }
                else
                    T.Info(rs + "/R1: independence of blind copies", Opt().Obs("only " + FmtI(static_cast<long>(nC)) + " comparable failed-then-copied samples").Note("not enough data for a verdict"));
            }
            std::string dist;
            for (const auto& [k, n] : r1CountDist)
            {
                dist += std::to_string(k) + " tx: " + FmtI(n) + "; ";
            }
            T.Info(rs + "/R1: transmissions per TB (the exact count check is A04)", Opt().Obs(dist));
            if (rs == "SYN_NEAR")
            {
                T.Check(rs + "/R1: copies are sent although every member decoded the first (blind definition)",
                        r1AllDecAtFirst > 0 && r1AllDecAtFirstWith3 == r1AllDecAtFirst,
                        Opt().Exp("100% of the all-decoded TBs have maxNumTx transmissions")
                            .Obs(FmtI(r1AllDecAtFirstWith3) + " of " + FmtI(r1AllDecAtFirst)).Sev("H"));
            }
        }
        if (nRuns4)
        {
            eq(rs + "/R4: the leader transmits exactly once per TB", r4Leader1, r4Tbs);
            T.Check(rs + "/R4: helper copies == maxNumTx-1 iff the helper decoded the initial transmission (else 0)",
                    r4Tbs > 0 && r4Iff == r4Tbs,
                    Opt().Exp(FmtI(r4Tbs) + " TBs").Obs(FmtI(r4Iff) + " conform; helper decoded " + FmtI(r4HelperDec) +
                                                        " TBs: with 0 copies " + FmtI(r4Dec0) + ", 1 copy " + FmtI(r4Dec1) +
                                                        ", 2 copies " + FmtI(r4DecFull) + "; copies sent without decode " +
                                                        FmtI(r4NotDecCopies))
                        .Tol("0").Sev("H").Fnd("F18")
                        .Note("separates the causes of F18: TBs with fewer copies than maxNumTx-1 are split into helper-not-decoded "
                              "(expected, no copy) and decoded-but-missing-copy (violation, listed in the artefact)"));
            eq(rs + "/R4: copies in slots without a leader PSSCH (S1 usurp)", r4Disjoint, r4Tbs);
            eq(rs + "/R4: copies after the initial transmission and within 32 slots", r4Win, r4Tbs, "M");
            eq(rs + "/R4: copies have the TB's rb_len", r4Rb, r4Tbs);
        }
    }
    const std::string art = V.Write("B01_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
