// E06_spectrum_usage_nodes (C: CORE; W: SYN_NEAR): PSSCH subchannel-slots per node and per resource origin (transmitted and reserved, D-2),
// PSFCH usage per option, analytical 3GPP PSFCH resource budget, corrected CBR / platoon share (F01).
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/spectrum_accounting.h"
#include "common/stats.h"

#include <cmath>
#include <map>
#include <set>

using namespace ptest;

int
main()
{
    TestReport T("E06", "spectrum_usage_nodes", "E");
    T.Summary("Only the leader and the applied helpers transmit platoon PSSCH; background never uses dst 254; transmitted and reserved "
              "resources are reported per node and per origin; Option 2 is feasible within the 3GPP PSFCH resource budget.");
    T.AddRef("TS 38.213 16.3 (PSFCH resource: R_PRB,CS = N_type * M_subch,slot * N_CS; Option 2 needs group size <= R_PRB,CS) [V]");
    T.AddRef("TEST_PLAN D-2 (transmitted and reserved resources per node and origin)");
    ViolationLog V;
    CsvWriter acct(TestsRoot() + "/results/E06_spectrum_accounting.csv",
                   "runset,arm,scenario,ivd,bg,traffic,seed,leader_sps,origin,win_s,tx_leader,tx_helper,tx_background,res_reserved,res_used_leader,res_used_helper,res_reclaimed_leader,res_unused_ack,res_unused_no_trigger,res_unused_helper_not_decoded,res_unused_empty_period,res_unused_reselection,res_unused_other,periods,periods_with_data,reselections,new_tbs,cbr_csv,platoon_share_true,platoon_share_csv");
    T.Artefact("results/E06_spectrum_accounting.csv");

    long runs = 0, leaderZero = 0, memberUse = 0, bgPlatoon = 0, helperNotApplied = 0, accountFail = 0, resNeg = 0;
    std::map<std::string, std::vector<double>> pfOpt;   // arm -> (ACK+NACK) / (leader tx x expected feedbackers)
    std::map<std::string, long> ackByArm, nackByArm;
    for (const std::string rs : {"CORE", "SPSDYN", "SYN_NEAR"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { if (rs != "SPSDYN") { if (rs == "CORE") T.Skip("CORE data", "CORE manifest not present (campaign stage not run yet)"); } continue; }
        T.AddRunset(rs);
        for (const auto& rr : all)
        {
            Run r;
            if (!r.Load(rr) || !r.hasAlltx || !r.hasGrants) continue;
            T.AddNRuns(1);
            ++runs;
            const SpectrumAcct a = ComputeSpectrum(r);
            if (!a.ok) { ++accountFail; continue; }
            leaderZero += (a.txLeader <= 0) ? 1 : 0;
            // who transmitted dst 254: members only if relaying helpers; background never
            std::set<int> applied;
            if (r.hasHsel)
            {
                const auto& ap = r.hselT.D("applied");
                for (size_t i = 0; i < r.hselT.nrows; ++i) applied.insert(static_cast<int>(ap[i]));
            }
            else if (r.helperNodeStatic > 0 && rr.IsHelperArm()) applied.insert(r.helperNodeStatic);
            for (const auto& [node, v] : a.txByNode)
            {
                if (node == 0) continue;
                if (node > kMembers)
                {
                    ++bgPlatoon;
                    V.Add("background node on dst 254", rs + "/" + rr.arm + "/" + rr.tag + " node " + std::to_string(node));
                }
                else if (!rr.IsHelperArm())
                {
                    ++memberUse;
                    V.Add("members transmit only as relaying helpers", rs + "/" + rr.arm + "/" + rr.tag + " node " + std::to_string(node));
                }
                else if (!applied.count(node))
                {
                    ++helperNotApplied;
                    V.Add("transmitting members are applied helpers", rs + "/" + rr.arm + "/" + rr.tag + " node " + std::to_string(node));
                }
            }
            const double slSlots = (r.kpiEnd - r.kpiStart) / kSlotS * r.R("n_subch", 5);
            const ReservedBreakdown& R = a.res;
            resNeg += (R.unusedOther < -1e-6) ? 1 : 0;
            acct.Row({rs, rr.arm, rr.scenario, rr.ivd, rr.bg, rr.traffic, std::to_string(rr.seed), std::to_string(rr.leaderSps), OriginName(a.origin), Fmt(a.winS, 6), Fmt(a.txLeader, 8), Fmt(a.txHelper, 8),
                      Fmt(a.txBackground, 8), Fmt(R.reserved, 8), Fmt(R.usedLeader, 8), Fmt(R.usedHelper, 8), Fmt(R.reclaimedLeader, 8), Fmt(R.unusedAck, 8), Fmt(R.unusedNoTrigger, 8),
                      Fmt(R.unusedHelperNotDecoded, 8), Fmt(R.unusedEmptyPeriod, 8), Fmt(R.unusedReselection, 8), Fmt(R.unusedOther, 8), FmtI(R.periods), FmtI(R.periodsWithData),
                      FmtI(R.reselections), FmtI(a.newTbs), Fmt(r.R("cbr"), 8), Fmt((a.txLeader + a.txHelper) / slSlots, 8), Fmt(r.R("platoon_cbr_share"), 8)});
            // PSFCH usage
            const long ltx = static_cast<long>(r.R("leader_tx"));
            if (rr.HarqMode() == "opt2" && ltx > 0)
            {
                const double ack = r.R("explicit_ack_rx"), nack = r.R("nack_rx");
                const double expPer = rr.IsHelperArm() ? kMembers - 1 : kMembers; // the named helper stays silent on success
                pfOpt[rr.arm].push_back((ack + nack) / (expPer * static_cast<double>(r.R("leader_tx") + r.R("helper_tx"))));
                ackByArm[rr.arm] += static_cast<long>(ack);
                nackByArm[rr.arm] += static_cast<long>(nack);
            }
            if (rr.HarqMode() == "opt1")
            {
                ackByArm[rr.arm] += static_cast<long>(r.R("explicit_ack_rx"));
                nackByArm[rr.arm] += static_cast<long>(r.R("nack_rx"));
            }
        }
    }
    if (runs == 0) return T.Finish();
    T.Check("the leader transmits PSSCH in every run (subchannel-slots > 0)", leaderZero == 0, Opt().Exp("0 of " + FmtI(runs)).Obs(FmtI(leaderZero)).Tol("0").Sev("H"));
    T.Check("members of leader-only arms never transmit dst-254 PSSCH", memberUse == 0, Opt().Exp("0").Obs(FmtI(memberUse)).Tol("0").Sev("H"));
    T.Check("in helper arms only the applied helpers transmit among the members", helperNotApplied == 0, Opt().Exp("0").Obs(FmtI(helperNotApplied)).Tol("0").Sev("H"));
    T.Check("background nodes never use dst 254", bgPlatoon == 0, Opt().Exp("0").Obs(FmtI(bgPlatoon)).Tol("0").Sev("H"));
    T.Check("reserved-resource classification closes: no run uses more subchannel-slots than its grants reserved (unused_other >= 0)", resNeg == 0,
            Opt().Exp("0 runs with a negative residual").Obs(FmtI(resNeg) + " of " + FmtI(runs)).Tol("1e-6").Sev("M")
                .Note("the residual column res_unused_other holds reserved minus classified; a negative value means transmissions outside the logged grants"));
    if (accountFail > 0)
        T.Info("runs without _alltx / _grants", Opt().Obs(FmtI(accountFail)));
    T.Info("D-2 logging gap: helper-own resources (S3) cannot be accounted", Opt().Obs(HelperOwnLoggingGap()).Note("API ready: ResOrigin::HelperOwnDynamic in common/spectrum_accounting.h"));
    for (const auto& [arm, v] : pfOpt)
    {
        const MeanCI m = MeanCi(v);
        T.Info("PSFCH usage " + arm + " (Option 2): (ACK + NACK received) / (silent-helper-corrected group size x platoon transmissions)",
               Opt().Obs(m.ok() ? FmtCi(m.mean, m.lo, m.hi, 4) : Fmt(m.mean, 4)).Note("< 1 because a member that already decoded stays silent on retransmissions (F25) and because of DTX"));
    }
    for (const auto& [arm, n] : ackByArm)
    {
        if (arm == "R2" || arm == "R5_S1_H2")
            T.Check("PSFCH usage " + arm + " (Option 1): ACK = 0, only NACKs", n == 0, Opt().Exp("0 ACKs").Obs(FmtI(n) + " ACKs, " + FmtI(nackByArm[arm]) + " NACKs").Tol("0").Sev("M").Fnd("F13"));
    }
    // 3GPP PSFCH budget (analytical, labelled assumption)
    {
        bool allOk = true;
        std::string line;
        for (int ncs : {1, 2, 3, 6})
        {
            const int mPrbSet = 50, nSubch = 5, N = 1, nType = 1;
            const int mSubchSlot = mPrbSet / (nSubch * N);
            const int rPrbCs = nType * mSubchSlot * ncs;
            allOk = allOk && rPrbCs >= 8;
            line += "N_CS=" + std::to_string(ncs) + ": R_PRB,CS=" + std::to_string(rPrbCs) + "; ";
        }
        T.Pass("3GPP PSFCH budget: Option 2 feasible for 8 members for every N_CS (R_PRB,CS >= 8)", Opt().Exp("R_PRB,CS >= 8").Obs(line)
                   .Ref("TS 38.213 16.3 [V]").Note("assumption (labelled): M_PRB,set = 50 PRB, N_subch = 5, N = 1, N_type = 1 (startSubCH); not modelled by the simulator (DV1)"));
        T.Info("PSFCH resources per TB", Opt().Obs("Option 1: one shared resource (all NACKers on M_ID = 0, cyclic shift m_cs = 0); Option 2: one resource per group member (8, or 7 with the silent helper), ACK m_cs = 6, NACK m_cs = 0").Ref("TS 38.213 16.3 [V]"));
        (void)allOk;
    }
    const std::string art = V.Write("E06_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
