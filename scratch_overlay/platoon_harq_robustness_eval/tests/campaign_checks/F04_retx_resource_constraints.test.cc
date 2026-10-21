// F04_retx_resource_constraints (C: CORE; W: SYN_MARGINAL, SYN_SPS): spacing of consecutive transmissions of a TB (PSFCH round trip +
// processing for HARQ arms), PDB bound of every transmission (F23), SCI-1A 32-slot window (DV8), own-reservation overlap at mu=1 (F14).
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <cmath>
#include <map>
#include <set>
#include <unordered_map>

using namespace ptest;

int
main()
{
    TestReport T("F04", "retx_resource_constraints", "F");
    T.Summary("Retransmission resources respect the HARQ round trip (>= K + 2 slots), the packet delay budget (F23), the 32-slot SCI-1A "
              "window (DV8) and the leader never reselects onto its own valid reservation (F14).");
    T.AddRef("TS 38.321 5.22.1.1 / TS 38.214 8.1.4 (resource selection with feedback: room for PSFCH + processing) [V]");
    T.AddRef("TEST_PLAN F14 (nr_sl_coop_scheduler.cc ~1841: GetFutureSfnSf(rri_ms*4) assumes mu = 2), F23, DV8");
    ViolationLog V;
    CsvWriter gaps(TestsRoot() + "/results/F04_gap_hist.csv", "runset,arm,family,gap_slots,count");
    T.Artefact("results/F04_gap_hist.csv");
    CsvWriter pdbTb(TestsRoot() + "/results/F04_pdb_tbs.csv", "runset,arm,traffic,leader_sps,seed,tb,tgen,ntx,first_minus_gen_ms,last_minus_gen_ms,late_ms,last_side,n_leader,n_helper");
    T.Artefact("results/F04_pdb_tbs.csv");
    const int K = 3, PROC = 2;
    std::map<std::string, std::map<int, long>> gapHist;

    for (const std::string rs : {"CORE", "SYN_MARGINAL", "SYN_SPS", "SYN_P2", "SPSDYN"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { if (rs == "CORE") T.Skip("CORE data", "CORE manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        struct Agg { long f32Tbs{0}, f32Late{0}; double f32Max{0}; long pdbSlotBad{0}; double maxLate{0}; long gapBadP2{0}, pdbBadP2{0}, pairs{0}, gapBad{0}, tbs{0}, pdbBad{0}, pdbTbs{0}, win32Bad{0}, notFit{0}, tbsP2{0}; long f14{0}, f14N{0}; };
        std::map<std::string, Agg> byArm;
        for (const auto& rr : all)
        {
            Run r;
            if (!r.Load(rr) || !r.hasAtt) continue;
            T.AddNRuns(1);
            Agg& A = byArm[rr.arm];
            const std::string family = rr.HarqMode() == "blind" ? "blind" : "harq";
            for (const TbView& tv : WindowTbViews(r))
            {
                ++A.tbs;
                A.tbsP2 += (rr.traffic.rfind("periodic2", 0) == 0);
                for (size_t k = 1; k < tv.all.size(); ++k)
                {
                    const int gap = static_cast<int>(tv.all[k]->slot - tv.all[k - 1]->slot);
                    gapHist[rs + "/" + rr.arm + "/" + family][std::min(gap, 40)]++;
                    if (family == "harq" && rr.Retx() != "")
                    {
                        ++A.pairs;
                        if (gap < K + PROC)
                        {
                            ++(rr.traffic.rfind("periodic2", 0) == 0 ? A.gapBadP2 : A.gapBad);
                            V.Add(rr.arm + " consecutive transmissions >= K + 2 slots apart", rs + "/" + rr.tag + " tb " + FmtI(tv.id) + " gap " + std::to_string(gap));
                        }
                    }
                }
                if (!std::isnan(tv.tGen))
                {
                    ++A.pdbTbs;
                    const double last = tv.all.back()->t;
                    if (rr.traffic.rfind("periodic2", 0) == 0)
                    {
                        pdbTb.Row({rs, rr.arm, rr.traffic, rr.leaderSps ? "1" : "0", std::to_string(rr.seed), FmtI(tv.id), Fmt(tv.tGen, 6), std::to_string(tv.all.size()),
                                   Fmt((tv.all.front()->t - tv.tGen) * 1e3, 4), Fmt((last - tv.tGen) * 1e3, 4), Fmt((last - tv.tGen - r.pdbS) * 1e3, 4),
                                   std::to_string(tv.all.back()->side), std::to_string(tv.leader.size()), std::to_string(tv.helper.size())});
                    }
                    if (P2fxNoT2(rr))
                    {
                        ++A.f32Tbs;
                        if (last > tv.tGen + r.pdbS + kSlotS + 1e-9) { ++A.f32Late; A.f32Max = std::max(A.f32Max, last - tv.tGen - r.pdbS); }
                    }
                    else if (last > tv.tGen + r.pdbS + 1e-9)
                    {
                        ++A.pdbBad;
                        A.maxLate = std::max(A.maxLate, last - tv.tGen - r.pdbS);
                        if (last > tv.tGen + r.pdbS + kSlotS + 1e-9) ++A.pdbSlotBad;
                        A.pdbBadP2 += (rr.traffic.rfind("periodic2", 0) == 0);
                        if (V.Count(rr.arm + " last transmission <= t_gen + PDB") < 20) V.Add(rr.arm + " last transmission <= t_gen + PDB", rs + "/" + rr.tag + " tb " + FmtI(tv.id) + " late by " + Fmt((last - tv.tGen - r.pdbS) * 1e3, 4) + " ms");
                    }
                }
                // SCI-1A window: every copy within 31 slots of the first
                for (const PTx* x : tv.all)
                {
                    if (x->slot - tv.initial->slot > 31)
                    {
                        ++A.win32Bad;
                        V.Add(rr.arm + " transmissions within 31 slots of the first (SCI-1A N_MAX window)", rs + "/" + rr.tag + " tb " + FmtI(tv.id) + " offset " + FmtI(x->slot - tv.initial->slot));
                        break;
                    }
                }
                // TBs that could not fit maxNumTx transmissions (blind: fewer copies; HARQ: not applicable)
                if (rr.traffic.rfind("periodic2", 0) == 0 && static_cast<int>(tv.all.size()) < r.maxNumTx && rr.HarqMode() == "blind" && rr.Retx() == "R1") ++A.notFit;
            }
            // F14 at mu = 1: new leader grant overlapping the leader's own still-valid reservation
            if (rr.leaderSps && r.hasGrants)
            {
                const auto& node = r.grantsT.D("node");
                const auto& ev = r.grantsT.S("event");
                const auto& sps = r.grantsT.D("sps");
                const auto& rri = r.grantsT.D("rri_ms");
                const auto& first = r.grantsT.D("first_slot");
                const auto& sbs = r.grantsT.D("sb_start");
                const auto& sbl = r.grantsT.D("sb_len");
                std::vector<size_t> c;
                for (size_t i = 0; i < r.grantsT.nrows; ++i) if (node[i] == 0 && ev[i] == "c" && sps[i] != 0) c.push_back(i);
                for (size_t j = 1; j < c.size(); ++j)
                {
                    const size_t o = c[j - 1], n = c[j];
                    const int64_t per = std::llround(rri[o] / 1000.0 / kSlotS);
                    if (per <= 0) continue;
                    const int64_t nf = static_cast<int64_t>(first[n]);
                    ++A.f14N;
                    // would the old chain's grid hit the new first slot?
                    if (nf >= static_cast<int64_t>(first[o]) && (nf - static_cast<int64_t>(first[o])) % per == 0)
                    {
                        const bool ov = sbs[n] < sbs[o] + sbl[o] && sbs[o] < sbs[n] + sbl[n];
                        if (ov) { ++A.f14; V.Add("F14 new grant overlaps the leader's own valid reservation", rs + "/" + rr.arm + "/" + rr.tag + " new first slot " + FmtI(nf)); }
                    }
                }
            }
        }
        for (const auto& [arm, A] : byArm)
        {
            const std::string pre = rs + " " + arm + ": ";
            if (A.pairs > 0)
            {
                T.Check(pre + "consecutive transmissions of a TB at least K + MinTimeGapProcessing = 5 slots apart (periodic1tb/aperiodic1tb)", A.gapBad == 0, Opt().Exp("0 of " + FmtI(A.pairs) + " pairs").Obs(FmtI(A.gapBad)).Tol("0").Sev("H").Ref("TS 38.214 8.1.4 [V]; NR scheduler IsMinTimeGapSatisfied"));
                if (A.gapBadP2 > 0)
                    T.Fail(pre + "periodic2tb: consecutive transmissions of a TB at least 5 slots apart", Opt().Fnd("F30").Sev("M").Exp("0 of " + FmtI(A.pairs) + " pairs").Obs(FmtI(A.gapBadP2) + " pairs closer than 5 slots (gap 0: the leader's spurious reclaim and the helper's copy in the same slot)")
                                .Note("new finding F30: a late NACK of the previous TB on the shared HARQ id triggers a reclaim of the next TB's handed slot (see C01 / C04 / B05)"));
            }
            if (A.f32Tbs > 0)
                T.Check(pre + "periodic2fx without --t2Ms: last transmission of every TB <= t_gen + PDB + 1 slot", A.f32Late == 0,
                        Opt().Fnd("F32").Sev("M").Exp("0 of " + FmtI(A.f32Tbs)).Obs(FmtI(A.f32Late) + " TBs later than PDB + 1 slot, max " + Fmt(A.f32Max * 1e3, 5) + " ms late").Tol("1 slot").Note("F32 (TEST_PLAN 0.4): periodic2fx without --t2Ms. period = RRI = PDB = 10 ms = 20 slots and the selection window is the full PDB counted from the scheduling slot (F23), so TB k's last reserved transmission can land after packet k+1 is generated; the SB-SPS grant keeps its HARQ process and the MAC sends the new packet in TB k's retransmission slot (one packet counted in two TBs, backlog, one-TB guard). Runs with --t2Ms=9 (T2 = 18 slots) must show 0"));
            if (A.pdbTbs > 0)
            {
                if (A.pdbBad == 0) T.Pass(pre + "last transmission of every TB <= t_gen + PDB", Opt().Exp("0 of " + FmtI(A.pdbTbs)).Obs("0").Fnd("F23").Tol("0"));
                else T.Fail(pre + "last transmission of every TB <= t_gen + PDB",
                                  Opt().Fnd("F23").Sev("L").Exp("0 of " + FmtI(A.pdbTbs)).Obs(FmtI(A.pdbBad) + " TBs have a transmission after the PDB (all periodic2tb: " + FmtI(A.pdbBadP2) + " of " + FmtI(A.tbsP2) + " periodic2tb TBs), max " + Fmt(A.maxLate * 1e3, 3) + " ms late").Tol("0")
                                      .Note("T2 is clipped to the PDB (nr-sl-ue-mac.cc:416-433: T2 = min(PDB, T2 attribute)), and the PDB reaches the LC (groupcast_flows -> LC info -> GetLcPdb in the scheduler, fixed-mcs.cc:970 / nr_sl_coop_scheduler.cc:1012), but the FULL PDB (20 slots) is passed, counted from the scheduling slot n (the first slot boundary after the packet was generated), not the remaining PDB (PDB - (n - t_gen)); TS 38.214 8.1.4 requires T2 <= remaining PDB.  The last copy therefore lands at n + 20 = 20.6-20.8 slots after generation, 0.3-0.4 ms (one slot) late. --t2Ms would not change this (the attribute 33 slots > PDB 20 slots, min() already picks the PDB); a correct fix passes the remaining PDB per packet; distribution of last_tx - t_gen: results/F04_pdb_tbs.csv (violators at 20.6 / 20.8 slots, SPS and dynamic leader alike: SYN_P2)"));
                T.Check(pre + "last transmission of every TB <= t_gen + PDB + 1 slot", A.pdbSlotBad == 0, Opt().Exp("0 of " + FmtI(A.pdbTbs)).Obs(FmtI(A.pdbSlotBad)).Tol("1 slot = 0.5 ms").Sev("M").Fnd("F23"));
            }
            T.Check(pre + "all transmissions of a TB within 31 slots of the first (SCI-1A 32-slot window)", A.win32Bad == 0, Opt().Exp("0").Obs(FmtI(A.win32Bad)).Tol("0").Sev("M").Fnd("DV8"));
            if (A.notFit > 0) T.Info(pre + "periodic2tb TBs that could not fit maxNumTx transmissions inside the PDB (blind)", Opt().Obs(FmtI(A.notFit) + " TBs").Fnd("F23"));
            if (A.f14N > 0)
            {
                if (A.f14 > 0) T.Xfail(pre + "F14: a new leader grant overlaps the leader's own still-valid periodic reservation", Opt().Fnd("F14").Sev("M").Exp("0").Obs(FmtI(A.f14) + " of " + FmtI(A.f14N) + " reselections")
                                            .Note("GetFutureSfnSf(rri_ms * 4) assumes 4 slots/ms (mu = 2); the study uses mu = 1"));
                else T.Pass(pre + "F14 not triggered in the study: no new grant overlaps the leader's own old reservation", Opt().Fnd("F14").Exp("0").Obs("0 of " + FmtI(A.f14N) + " reselections"));
            }
        }
    }
    for (const auto& [k, h] : gapHist)
    {
        const auto p1 = k.find('/'), p2 = k.rfind('/');
        for (const auto& [g, n] : h) gaps.Row({k.substr(0, p1), k.substr(p1 + 1, p2 - p1 - 1), k.substr(p2 + 1), std::to_string(g), std::to_string(n)});
    }
    const std::string art = V.Write("F04_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
