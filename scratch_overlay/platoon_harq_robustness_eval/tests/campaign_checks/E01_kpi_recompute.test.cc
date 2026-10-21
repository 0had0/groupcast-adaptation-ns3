// E01_kpi_recompute (C: CORE, SPSDYN; W: all synthetic): independent recomputation of the KPIs of _member.csv / _run.csv from the raw
// logs; the spectrum metrics res_used_helper / res_per_pkt_used of the helper-less arms are wrong (F01).
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
    TestReport T("E01", "kpi_recompute", "E");
    T.Summary("Per-member PRR / loss causes, transmission counts, CBR and the attempt counters are recomputed from the raw logs and "
              "compared with the run's own KPI files; res_used_helper and res_per_pkt_used of the leader-only arms are wrong (F01).");
    T.AddRef("TEST_PLAN F01 (main.cc:3985-3993), F16 (main.cc:2926-2978), main.cc:2689-2722 (CBR)");
    ViolationLog V;
    CsvWriter f01(TestsRoot() + "/results/E01_f01_res_error.csv", "runset,arm,scenario,ivd,bg,traffic,seed,res_used_leader_csv,res_used_helper_csv,res_used_helper_true,res_per_pkt_used_csv,res_per_pkt_used_true,rel_error");
    T.Artefact("results/E01_f01_res_error.csv");

    struct Agg
    {
        long runs{0};
        long memberBad{0}, memberCells{0}, runBad{0}, txBad{0}, cbrBad{0}, attBad{0}, attCells{0}, tpkBad{0};
        long redundantDec{0}, attemptsTot{0}, helperAttOk{0}, helperAttOkRedundant{0};
        long f01Runs{0}, f01Wrong{0};
        std::vector<double> relErr;
    };
    std::map<std::string, Agg> byArm;

    const std::vector<std::string> sets = {"CORE", "SPSDYN", "SYN_NEAR", "SYN_MARGINAL", "SYN_DTX", "SYN_MCR", "SYN_DIST", "SYN_SPS"};
    for (const std::string& rs : sets)
    {
        const auto all = LoadManifest(rs);
        if (all.empty())
        {
            if (rs == "CORE" || rs == "SPSDYN")
            {
                T.Skip(rs + " data", rs + " manifest not present (campaign stage not run yet)");
            }
            continue;
        }
        T.AddRunset(rs);
        for (const auto& rr : all)
        {
            Run r;
            if (!r.Load(rr) || !r.hasAtt || !r.hasAlltx)
            {
                continue;
            }
            T.AddNRuns(1);
            Agg& A = byArm[rr.arm];
            ++A.runs;
            const std::string where = rs + "/" + rr.arm + "/" + rr.tag;
            // ---- per-member KPIs from _pkt ---------------------------------------------------------------------------------------
            double prrSum = 0, prrMin = 1;
            for (int m = 1; m <= kMembers; ++m)
            {
                const auto& rows = r.pkts[m - 1];
                long sent = static_cast<long>(rows.size()), rx = 0, rxPdb = 0, c1 = 0, c2 = 0, c3 = 0, c4 = 0;
                for (const auto& p : rows)
                {
                    rx += p.rx;
                    rxPdb += (p.rx && p.delayMs / 1e3 <= r.pdbS + 1e-9) ? 1 : 0;
                    c1 += (p.cause == 1);
                    c2 += (p.cause == 2);
                    c3 += (p.cause == 3);
                    c4 += (p.cause == 4);
                }
                const double S = std::max<double>(1.0, sent);
                struct { const char* col; double mine; double tol; } chk[] = {
                    {"sent", static_cast<double>(sent), 0}, {"rx", static_cast<double>(rx), 0}, {"prr", rx / S, 5e-6},
                    {"prr_pdb", rxPdb / S, 5e-6}, {"late", (rx - rxPdb) / S, 5e-6},
                    {"lost_collision", static_cast<double>(c1), 0}, {"lost_channel", static_cast<double>(c2), 0},
                    {"lost_notx", static_cast<double>(c3), 0}, {"lost_other", static_cast<double>(c4), 0}};
                for (const auto& c : chk)
                {
                    ++A.memberCells;
                    if (std::fabs(r.MemberCol(c.col, m) - c.mine) > c.tol)
                    {
                        ++A.memberBad;
                        V.Add("member KPI recompute", where + " member " + std::to_string(m) + " " + c.col + " csv " + Fmt(r.MemberCol(c.col, m), 8) + " mine " + Fmt(c.mine, 8));
                    }
                }
                prrSum += rx / S / kMembers;
                prrMin = std::min(prrMin, rx / S);
            }
            if (std::fabs(prrSum - r.R("prr_mean")) > 5e-6 || std::fabs(prrMin - r.R("prr_min")) > 5e-6)
            {
                ++A.runBad;
                V.Add("prr_mean / prr_min recompute", where + " csv " + Fmt(r.R("prr_mean"), 8) + "/" + Fmt(r.R("prr_min"), 8) + " mine " + Fmt(prrSum, 8) + "/" + Fmt(prrMin, 8));
            }
            // ---- transmission counts ------------------------------------------------------------------------------------------------
            long lTx = 0, lNew = 0, lRetx = 0, hTx = 0;
            for (const PTx& x : r.txs)
            {
                if (!r.InWin(x.t))
                {
                    continue;
                }
                if (x.side == 0)
                {
                    ++lTx;
                    (x.txInTb == 1 ? lNew : lRetx)++;
                }
                else
                {
                    ++hTx;
                }
            }
            // +-1 at the window edge: the roundabout runs end with the trace (kpi end = t_end - 0.3 s computed from a printed value), so one transmission at the very last slot can fall on either side
            if (std::labs(lTx - static_cast<long>(r.R("leader_tx"))) > 1 || std::labs(lNew - static_cast<long>(r.R("leader_new_tb"))) > 1 ||
                std::labs(lRetx - static_cast<long>(r.R("leader_retx"))) > 1 || std::labs(hTx - static_cast<long>(r.R("helper_tx"))) > 1)
            {
                ++A.txBad;
                V.Add("transmission counts recompute", where + " csv " + Fmt(r.R("leader_tx"), 6) + "/" + Fmt(r.R("leader_new_tb"), 6) + "/" + Fmt(r.R("leader_retx"), 6) + "/" + Fmt(r.R("helper_tx"), 6) +
                                                           " mine " + FmtI(lTx) + "/" + FmtI(lNew) + "/" + FmtI(lRetx) + "/" + FmtI(hTx));
            }
            // tx_per_pkt: transmissions of the TBs of the window packets / packets
            {
                long txs = 0;
                for (const auto& [id, tb] : r.tbs)
                {
                    if (!std::isnan(tb.tGen))
                    {
                        txs += static_cast<long>(tb.tx.size());
                    }
                }
                const double pk = r.R("pkts");
                const double mine = pk > 0 ? static_cast<double>(txs) / pk : 0.0;
                if (std::fabs(mine - r.R("tx_per_pkt")) > 2e-2)
                {
                    ++A.tpkBad;
                    V.Add("tx_per_pkt recompute", where + " csv " + Fmt(r.R("tx_per_pkt"), 6) + " mine " + Fmt(mine, 6));
                }
            }
            // ---- CBR and spectrum ---------------------------------------------------------------------------------------------------
            const SpectrumAcct sp = ComputeSpectrum(r);
            double busy = 0, leader = 0;
            std::set<int64_t> occ;
            {
                const int64_t s0 = std::llround(r.kpiStart / kSlotS), s1 = std::llround(r.kpiEnd / kSlotS);
                const auto& slot = r.alltxT.D("slot_abs");
                const auto& rbs = r.alltxT.D("rb_start");
                const auto& rbl = r.alltxT.D("rb_len");
                const auto& node = r.alltxT.D("node");
                const auto& x = r.alltxT.D("x");
                const auto& y = r.alltxT.D("y");
                for (size_t i = 0; i < r.alltxT.nrows; ++i)
                {
                    const int64_t sl = static_cast<int64_t>(slot[i]);
                    if (sl < s0 || sl >= s1 || std::hypot(x[i], y[i]) > 5.0e4)
                    {
                        continue;
                    }
                    busy += rbl[i] / kSubchPrbs;
                    const int k0 = static_cast<int>(rbs[i]) / kSubchPrbs, k1 = (static_cast<int>(rbs[i] + rbl[i]) + kSubchPrbs - 1) / kSubchPrbs;
                    for (int k = k0; k < k1; ++k) occ.insert(sl * 1024 + k);
                    if (node[i] == 0) leader += rbl[i] / kSubchPrbs;
                }
            }
            const double slSlots = (r.kpiEnd - r.kpiStart) / kSlotS;
            const double totalSub = r.R("n_subch", 5);
            const double cbr = busy / (slSlots * totalSub), cbrLeader = leader / (slSlots * totalSub), cbrOcc = occ.size() / (slSlots * totalSub);
            if (std::fabs(cbr - r.R("cbr")) > 1e-5 || std::fabs(cbrLeader - r.R("cbr_leader")) > 1e-5 || std::fabs(cbrOcc - r.R("cbr_occupied")) > 1e-5)
            {
                ++A.cbrBad;
                V.Add("cbr / cbr_leader / cbr_occupied recompute", where + " csv " + Fmt(r.R("cbr"), 7) + "/" + Fmt(r.R("cbr_leader"), 7) + "/" + Fmt(r.R("cbr_occupied"), 7) +
                                                                      " mine " + Fmt(cbr, 7) + "/" + Fmt(cbrLeader, 7) + "/" + Fmt(cbrOcc, 7));
            }
            // F01: res_used_helper
            {
                const double usedL = sp.txLeader, usedHtrue = sp.txHelper;
                const double csvL = r.R("res_used_leader"), csvH = r.R("res_used_helper");
                const double pk = r.R("pkts");
                const double perCsv = r.R("res_per_pkt_used"), perTrue = pk > 0 ? (usedL + usedHtrue) / pk : 0.0;
                ++A.f01Runs;
                const bool wrong = std::fabs(csvH - usedHtrue) > 1e-6;
                A.f01Wrong += wrong ? 1 : 0;
                A.relErr.push_back(perTrue > 0 ? (perCsv - perTrue) / perTrue : 0.0);
                f01.Row({rs, rr.arm, rr.scenario, rr.ivd, rr.bg, rr.traffic, std::to_string(rr.seed), Fmt(csvL, 8), Fmt(csvH, 8), Fmt(usedHtrue, 8), Fmt(perCsv, 8), Fmt(perTrue, 8),
                         Fmt(perTrue > 0 ? (perCsv - perTrue) / perTrue : 0.0, 6)});
                if (std::fabs(csvL - usedL) > 1e-6)
                {
                    V.Add("res_used_leader recompute", where + " csv " + Fmt(csvL, 8) + " mine " + Fmt(usedL, 8));
                }
            }
            // ---- attempts (F16) ------------------------------------------------------------------------------------------------------
            {
                std::vector<long> att(kMembers, 0), ok(kMembers, 0), cor(kMembers, 0), mis(kMembers, 0);
                for (const auto& [id, tb] : r.tbs)
                {
                    if (std::isnan(tb.tGen))
                    {
                        continue;
                    }
                    for (int m = 1; m <= kMembers; ++m)
                    {
                        bool dec = false;
                        for (int u : tb.tx)
                        {
                            const PTx& x = r.txs[u];
                            if (r.IsSelf(x, m))
                            {
                                continue;
                            }
                            const int st = x.status[m - 1];
                            if (st == 0 && dec)
                            {
                                continue;
                            }
                            ++att[m - 1];
                            if (st == 1)
                            {
                                if (dec)
                                {
                                    ++A.redundantDec;
                                }
                                ++ok[m - 1];
                                dec = true;
                            }
                            else if (st == 2)
                            {
                                ++cor[m - 1];
                            }
                            else
                            {
                                ++mis[m - 1];
                            }
                            ++A.attemptsTot;
                        }
                    }
                }
                for (int m = 1; m <= kMembers; ++m)
                {
                    ++A.attCells;
                    if (att[m - 1] != static_cast<long>(r.MemberCol("attempts", m)) || ok[m - 1] != static_cast<long>(r.MemberCol("att_ok", m)) ||
                        cor[m - 1] != static_cast<long>(r.MemberCol("att_corrupt", m)) || mis[m - 1] != static_cast<long>(r.MemberCol("att_missed", m)))
                    {
                        ++A.attBad;
                        V.Add("attempt counters recompute", where + " member " + std::to_string(m) + " csv " + Fmt(r.MemberCol("attempts", m), 6) + "/" + Fmt(r.MemberCol("att_ok", m), 6) + " mine " + FmtI(att[m - 1]) + "/" + FmtI(ok[m - 1]));
                    }
                }
                A.helperAttOk += static_cast<long>(r.R("helper_att_ok", 0));
                A.helperAttOkRedundant += static_cast<long>(r.R("helper_att_ok", 0) - r.R("helper_recovered", 0));
            }
        }
    }
    if (byArm.empty())
    {
        return T.Finish();
    }
    Agg tot;
    for (const auto& [arm, A] : byArm)
    {
        tot.runs += A.runs; tot.memberBad += A.memberBad; tot.memberCells += A.memberCells; tot.runBad += A.runBad; tot.txBad += A.txBad;
        tot.cbrBad += A.cbrBad; tot.attBad += A.attBad; tot.attCells += A.attCells; tot.tpkBad += A.tpkBad;
        tot.redundantDec += A.redundantDec; tot.attemptsTot += A.attemptsTot;
    }
    T.Check("per-member sent/rx/prr/prr_pdb/late/loss causes equal the recount from _pkt.csv", tot.memberBad == 0,
            Opt().Exp("0 of " + FmtI(tot.memberCells) + " cells differ (counts exact, ratios 5e-6)").Obs(FmtI(tot.memberBad) + " in " + FmtI(tot.runs) + " runs").Tol("counts 0, ratios 5e-6 (6 printed digits)").Sev("H"));
    T.Check("prr_mean / prr_min of _run.csv equal the recount", tot.runBad == 0, Opt().Exp("0").Obs(FmtI(tot.runBad)).Tol("5e-6").Sev("H"));
    T.Check("leader_tx / leader_new_tb / leader_retx / helper_tx equal the recount from _ltx/_htx", tot.txBad == 0, Opt().Exp("0").Obs(FmtI(tot.txBad)).Tol("+-1 at the KPI window edge").Sev("H").Note("the pilot/reduced mismatches were 3 roundabout whole-trace runs, each off by exactly one new TB (csv 11186/5131 vs 11185/5130, 5090 vs 5089, 6300/5131 vs 6299/5130): a transmission at the last slot of the window"));
    T.Check("tx_per_pkt equals (transmissions of the window packets' TBs) / packets", tot.tpkBad == 0, Opt().Exp("0").Obs(FmtI(tot.tpkBad)).Tol("0.02 (TB/packet window boundary)").Sev("M").Fnd("F32") .Note("All failing runs are periodic2fx without --t2Ms (E01_violations.csv): the TB-to-packet attribution breaks when a retransmission slot carries the next packet. F32 (TEST_PLAN 0.4): periodic2fx without --t2Ms. period = RRI = PDB = 10 ms = 20 slots and the selection window is the full PDB counted from the scheduling slot (F23), so TB k's last reserved transmission can land after packet k+1 is generated; the SB-SPS grant keeps its HARQ process and the MAC sends the new packet in TB k's retransmission slot (one packet counted in two TBs, backlog, one-TB guard). Runs with --t2Ms=9 (T2 = 18 slots) must show 0"));
    T.Check("cbr, cbr_leader, cbr_occupied equal the recount from _alltx.csv (main.cc:2689-2722)", tot.cbrBad == 0, Opt().Exp("0").Obs(FmtI(tot.cbrBad)).Tol("1e-5").Sev("H"));
    T.Check("attempts / att_ok / att_corrupt / att_missed equal the recount from _att.csv", tot.attBad == 0,
            Opt().Exp("0 of " + FmtI(tot.attCells)).Obs(FmtI(tot.attBad)).Tol("0").Sev("M").Fnd(tot.attBad > 0 ? "F32" : "F16") .Note("All failing runs are periodic2fx without --t2Ms (E01_violations.csv): the TB-to-packet attribution breaks when a retransmission slot carries the next packet. F32 (TEST_PLAN 0.4): periodic2fx without --t2Ms. period = RRI = PDB = 10 ms = 20 slots and the selection window is the full PDB counted from the scheduling slot (F23), so TB k's last reserved transmission can land after packet k+1 is generated; the SB-SPS grant keeps its HARQ process and the MAC sends the new packet in TB k's retransmission slot (one packet counted in two TBs, backlog, one-TB guard). Runs with --t2Ms=9 (T2 = 18 slots) must show 0"));
    T.Info("F16: decodes of a TB the member already holds, counted as attempts / att_ok",
           Opt().Fnd("F16").Obs(FmtI(tot.redundantDec) + " redundant decodes among " + FmtI(tot.attemptsTot) + " attempts (" + Fmt(tot.attemptsTot ? 100.0 * tot.redundantDec / tot.attemptsTot : 0.0, 3) + " %)")
               .Note("helper_recovered / helper_att_ok also count copies that arrive after the member decoded through another transmission"));
    // F01 per arm
    for (const auto& [arm, A] : byArm)
    {
        if (A.f01Runs == 0)
        {
            continue;
        }
        const MeanCI e = MeanCi(A.relErr);
        double maxAbs = 0;
        for (double v : A.relErr) maxAbs = std::max(maxAbs, std::fabs(v));
        T.Check("F01 (fixed)/" + arm + ": res_used_helper of run.csv equals the recount (PSSCH of members 1..N) and res_per_pkt_used equals (leader + helper transmissions) / packets", A.f01Wrong == 0 && maxAbs < 1e-4,
                Opt().Fnd("F01").Sev("H").Exp("0 of " + FmtI(A.f01Runs) + " runs differ")
                    .Obs(FmtI(A.f01Wrong) + " of " + FmtI(A.f01Runs) + " runs differ; res_per_pkt_used relative error mean " + Fmt(e.mean, 4) + ", max |error| " + Fmt(maxAbs, 4))
                    .Tol("1e-6 on res_used_helper, 1e-4 relative on res_per_pkt_used")
                    .Note("TEST_PLAN 0.2: res_used_helper now sums the PSSCH of members 1..numMembers (relayed copies), not node --helper.  Before the fix: relative error +100 % for R1-R3 (the helper term re-counted the leader) and "
                          "-40 % to -64 % for the helper arms (see G03 / tests/out_prefix_v1)"));
        T.Metric("f01_relerr_" + arm, e.mean);
    }
    const std::string art = V.Write("E01_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
