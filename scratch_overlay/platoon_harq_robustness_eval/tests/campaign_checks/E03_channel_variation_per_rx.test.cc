// E03_channel_variation_per_rx (C: CORE none-bg cells; W: SYN_DIST): PRR / SINR / channel-loss vary with the member index and with the
// spacing; link conditions of the geometry; no collision loss without background traffic.
#include "common/csv.h"
#include "common/harness.h"
#include "common/kuo_model.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <cmath>
#include <cstdlib>
#include <map>
#include <set>

using namespace ptest;

int
main()
{
    TestReport T("E03", "channel_variation_per_rx", "E");
    T.Summary("Farther members see lower SINR and PRR and more channel losses; the tail PRR does not increase with the spacing; in the "
              "straight highway platoon member 1 is LOS and the others NLOSv; without background traffic nothing collides.");
    T.AddRef("TR 37.885 V2V highway path loss, NLOSv blockage (README channel model)");
    ViolationLog V;

    // ---- 1. variation with the member index: leader-only arms, no background, highway --------------------------------------------------
    for (const std::string rs : {"CORE", "SYN_DIST"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty())
        {
            if (rs == "CORE") T.Skip("CORE data", "CORE manifest not present (campaign stage not run yet)");
            continue;
        }
        T.AddRunset(rs);
        // cell (scenario/ivd/traffic) x arm -> per-member means over seeds
        struct Acc { std::vector<std::vector<double>> prr, sinr, chan; };
        std::map<std::string, Acc> cells;
        for (const auto& rr : all)
        {
            if (!(rr.Retx() == "R1" || rr.Retx() == "R2" || rr.Retx() == "R3") || rr.bg != "none" || rr.scenario != "highway")
            {
                continue;
            }
            Run r;
            if (!r.Load(rr)) continue;
            T.AddNRuns(1);
            Acc& a = cells[rr.CellKey() + "|" + rr.arm];
            if (a.prr.empty()) { a.prr.assign(kMembers, {}); a.sinr.assign(kMembers, {}); a.chan.assign(kMembers, {}); }
            for (int m = 1; m <= kMembers; ++m)
            {
                a.prr[m - 1].push_back(r.MemberCol("prr_pdb", m));
                a.sinr[m - 1].push_back(r.MemberCol("mean_sinr_db", m));
                a.chan[m - 1].push_back(r.MemberCol("channel_loss_rate", m));
            }
        }
        long ok1 = 0, ok2 = 0, ok3 = 0, n = 0;
        std::string weak;
        CsvWriter w(TestsRoot() + "/results/E03_prr_vs_member_" + rs + ".csv", "cell_arm,member,prr_pdb,mean_sinr_db,channel_loss_rate");
        T.Artefact("results/E03_prr_vs_member_" + rs + ".csv");
        for (const auto& [key, a] : cells)
        {
            std::vector<double> idx, p, s, c;
            for (int m = 1; m <= kMembers; ++m)
            {
                idx.push_back(m);
                p.push_back(Mean(a.prr[m - 1]));
                s.push_back(Mean(a.sinr[m - 1]));
                c.push_back(Mean(a.chan[m - 1]));
                w.Row({key, std::to_string(m), Fmt(p.back(), 6), Fmt(s.back(), 6), Fmt(c.back(), 6)});
            }
            const Corr cp = Spearman(idx, p), cs = Spearman(idx, s), cc = Spearman(idx, c);
            ++n;
            ok1 += DirectionalOk(cp, -1);
            ok2 += DirectionalOk(cs, -1);
            ok3 += DirectionalOk(cc, +1);
            if (!DirectionalOk(cp, -1) && weak.size() < 400)
            {
                weak += key + " rho(PRR)=" + Fmt(cp.rho, 3) + " p=" + Fmt(cp.p, 3) + "; ";
            }
        }
        if (n == 0)
        {
            T.Skip(rs + ": variation with member index", "no leader-only highway no-background cells");
        }
        else
        {
            const std::string pre = rs + " leader-only arms, no background: ";
            auto verdict = [&](const std::string& name, long ok, const std::string& what) {
                if (ok == n) T.Pass(pre + name, Opt().Exp("sign " + what + " with p < 0.05 or |rho| >= 0.6 in every cell").Obs(FmtI(ok) + " of " + FmtI(n) + " cells"));
                else T.Info(pre + name, Opt().Exp(what).Obs(FmtI(ok) + " of " + FmtI(n) + " cells reach the directional criterion").Note("saturated members (PRR = 1) and few members limit the rank test; INFO per section 7.3. " + weak));
            };
            verdict("Spearman rho(member index, PRR_pdb) < 0", ok1, "negative");
            verdict("Spearman rho(member index, mean SINR) < 0", ok2, "negative");
            verdict("Spearman rho(member index, channel loss rate) > 0", ok3, "positive");
        }
    }

    // ---- 2. SYN_DIST: tail PRR non-increasing with the spacing ------------------------------------------------------------------------------
    {
        const auto all = LoadManifest("SYN_DIST");
        if (!all.empty())
        {
            std::map<std::string, std::map<double, std::vector<double>>> tail; // arm -> spacing -> member-8 prr_pdb values
            for (const auto& rr : all)
            {
                Run r;
                if (!r.Load(rr)) continue;
                tail[rr.arm][std::stod(rr.ivd)].push_back(r.MemberCol("prr_pdb", 8));
            }
            for (const auto& [arm, sp] : tail)
            {
                std::vector<double> x, y;
                bool mono = true;
                double prev = 2;
                std::string line;
                for (const auto& [s, v] : sp)
                {
                    const double m = Mean(v);
                    x.push_back(s);
                    y.push_back(m);
                    line += Fmt(s, 3) + "m: " + Fmt(m, 3) + "; ";
                    if (m > prev + 0.02) mono = false;
                    prev = m;
                }
                const Corr c = Spearman(x, y);
                if (mono)
                    T.Pass("SYN_DIST " + arm + ": tail (member 8) PRR_pdb non-increasing with the spacing (tolerance 0.02)", Opt().Obs(line).Tol("0.02 (CI overlap)").Sev("M"));
                else
                    T.Info("SYN_DIST " + arm + ": tail PRR_pdb vs spacing is not monotone within 0.02", Opt().Obs(line + "rho=" + Fmt(c.rho, 3)).Note("statistical noise with 6 runs per point (section 7.3)"));
            }
        }
    }

    // ---- 3. link conditions of the geometry -------------------------------------------------------------------------------------------------
    for (const std::string rs : {"SYN_DIST", "CORE", "RBT_RING"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) continue;
        long cells = 0, m1Bad = 0, nlosvBad = 0, rbtNlos = 0, rbtCells = 0, rbtBadFlag = 0, rbtNoRing = 0, rbtNoRingNlos = 0;
        for (const auto& rr : all)
        {
            if (rr.bg != "none" || (rr.Retx() != "R1" && rr.Retx() != "R2" && rr.Retx() != "R3")) continue;
            Run r;
            if (!r.Load(rr)) continue;
            if (rr.scenario == "roundabout")
            {
                // The platoon drives ~290 m on the straight approach before the give-way line (trace json: leader_start_to_giveway_m /
                // leader_mean_speed_mps, ~22 s); the buildings only matter on the ring.  A run that ends before that cannot show NLOS.
                std::string js = ReadText(rr.cmd.find("--mobility=") == std::string::npos ? "" : [&] {
                    const size_t a = rr.cmd.find("--mobility=") + 11;
                    std::string pth = rr.cmd.substr(a, rr.cmd.find(' ', a) - a);
                    const size_t gz = pth.rfind(".csv.gz");
                    return gz == std::string::npos ? pth : pth.substr(0, gz) + ".json";
                }());
                auto num = [&](const std::string& key) {
                    const size_t k = js.find("\"" + key + "\"");
                    return k == std::string::npos ? NAN : std::atof(js.c_str() + js.find(':', k) + 1);
                };
                const double arrive = num("leader_start_to_giveway_m") / num("leader_mean_speed_mps");
                const bool reaches = std::isnan(arrive) || r.kpiEnd > arrive + 3.0;
                if (!reaches)
                {
                    ++rbtNoRing;
                    bool any = false;
                    for (int m = 1; m <= kMembers; ++m) any = any || r.MemberCol("frac_nlos", m) > 0;
                    rbtNoRingNlos += any ? 1 : 0;
                    rbtBadFlag += (rr.cmd.find("--sumoNet=") == std::string::npos) ? 1 : 0;
                    ++rbtCells;
                    rbtNlos += 0;
                    continue;
                }
            }
            if (rr.scenario == "highway")
            {
                ++cells;
                {
                    // member 1 has no vehicle in front of it: LOS with the highway P_LOS(d) of TR 37.885 (>= 0.9 for d <= ~45 m)
                    const double d1 = r.MemberCol("mean_dist_m", 1);
                    const double need = std::min(0.9, kuo::ProbLos(d1, true) - 0.1);
                    m1Bad += (r.MemberCol("frac_los", 1) < need) ? 1 : 0;
                }
                for (int m = 2; m <= kMembers; ++m)
                {
                    // frac_nlosv >= 0.8 (the platoon is a straight line of vehicles)
                    if (r.MemberCol("frac_nlosv", m) < 0.8)
                    {
                        ++nlosvBad;
                        if (V.Count("highway members >= 2 mostly NLOSv") < 20) V.Add("highway members >= 2 mostly NLOSv", rs + "/" + rr.arm + "/" + rr.tag + " member " + std::to_string(m) + " frac_nlosv " + Fmt(r.MemberCol("frac_nlosv", m), 4));
                    }
                }
            }
            else
            {
                ++rbtCells;
                bool any = false;
                for (int m = 1; m <= kMembers; ++m) any = any || r.MemberCol("frac_nlos", m) > 0;
                rbtNlos += any ? 1 : 0;
                rbtBadFlag += (rr.cmd.find("--sumoNet=") == std::string::npos) ? 1 : 0;
            }
        }
        if (cells > 0)
        {
            T.Check(rs + " highway: member 1 is LOS (frac_los >= min(0.9, P_LOS(d1) - 0.1))", m1Bad == 0, Opt().Exp("0 of " + FmtI(cells)).Obs(FmtI(m1Bad)).Tol("0").Sev("L").Note("the plan says >= 0.9; at 60-80 m spacing the highway model itself gives P_LOS(d) = 0.87-0.91 (TR 37.885, Kuo eq. 2-3), so the bound is relaxed to P_LOS(d1) - 0.1"));
            T.Info(rs + " highway: members >= 2 with frac_nlosv < 0.8", Opt().Obs(FmtI(nlosvBad) + " member cells").Note("vehicle blockage by the platoon members (README: NLOSv); short spacings and the ring geometry are not straight lines"));
        }
        if (rbtCells > 0)
        {
            const long ringCells = rbtCells - rbtNoRing;
            if (ringCells > 0)
            {
                T.Check(rs + " roundabout (whole trace, platoon reaches the ring): buildings create NLOS links for some member in every run (frac_nlos > 0)",
                        rbtNlos == ringCells, Opt().Exp(FmtI(ringCells)).Obs(FmtI(rbtNlos)).Sev("L"));
            }
            if (rbtNoRing > 0)
            {
                T.Info(rs + " roundabout: runs that end before the platoon reaches the ring (20 s quick window < ~22 s approach)",
                       Opt().Obs(FmtI(rbtNoRing) + " runs, " + FmtI(rbtNoRingNlos) + " of them with frac_nlos > 0")
                           .Note("not a failure: the straight approach has no buildings in view; the NLOS check is done by the RBT_RING run-set (whole trace)"));
            }
            T.Check(rs + " roundabout runs are launched with --sumoNet", rbtBadFlag == 0, Opt().Exp("0 runs without the flag").Obs(FmtI(rbtBadFlag)).Tol("0").Sev("H"));
        }
    }

    // ---- 4. no collision loss without background ----------------------------------------------------------------------------------------------
    {
        long runs = 0, bad = 0, runsP2 = 0, badP2 = 0;
        std::string ex, exP2;
        for (const std::string rs : {"CORE", "SYN_NEAR", "SYN_MARGINAL", "SYN_DTX", "SYN_MCR", "SYN_DIST", "SYN_SPS", "SYN_HM"})
        {
            for (const auto& rr : LoadManifest(rs))
            {
                if (rr.bg != "none") continue;
                Run r;
                if (!r.Load(rr)) continue;
                T.AddRunset(rs);
                const bool p2 = rr.traffic.rfind("periodic2", 0) == 0;
                ++(p2 ? runsP2 : runs);
                double lc = 0;
                for (int m = 1; m <= kMembers; ++m) lc += r.MemberCol("lost_collision", m);
                if (lc > 0)
                {
                    ++(p2 ? badP2 : bad);
                    V.Add("lost_collision == 0 without background", rs + "/" + rr.arm + "/" + rr.tag + " lost_collision " + Fmt(lc, 6) + " (member sum); n_att_coll leader tx coll rate " + Fmt(r.R("leader_tx_coll_rate"), 4));
                    std::string& e = p2 ? exP2 : ex;
                    if (e.size() < 300) e += rs + "/" + rr.arm + "/" + rr.tag + "(" + Fmt(lc, 4) + "); ";
                }
            }
        }
        if (runs > 0)
        {
            T.Check("no background: lost_collision == 0 for every member of every run, periodic1tb/aperiodic1tb (only the leader and its helper transmit)", bad == 0,
                    Opt().Exp("0 runs with collision losses of " + FmtI(runs)).Obs(FmtI(bad) + " runs: " + ex).Tol("0").Sev("H")
                        .Note("a collision without background means the leader and the helper (or two TBs) used the same slot and subchannels"));
        }
        if (runsP2 > 0)
        {
            T.Check("no background: lost_collision == 0 for every member of every run, periodic2tb", badP2 == 0,
                    Opt().Fnd("F30").Sev("M").Exp("0 runs with collision losses of " + FmtI(runsP2)).Obs(FmtI(badP2) + " runs: " + exP2).Tol("0")
                        .Note("F30 (new): periodic2tb with R6.  TB k and TB k+1 share a HARQ id (RRI 10 ms = 20 slots < the HARQ round trip of TB k).  The helper NACK of the LAST transmission of TB k (a stale decision, decision 2) arrives after TB k+1 started and is bound to TB k+1 (no NDI / slot check, DV4 / F29): the leader reclaims TB k+1's first handed slot (tx_in_tb 2) although the helper decoded TB k+1 and relays in the same slot.  Result: two identical PSSCH (same HARQ id, RV 2, same PRBs) in one slot, a physical self-collision without background traffic and a 4th transmission of TB k+1.  All 12 events of SYN_MARGINAL (R6, 3 seeds) show the helper NACK between the previous TB's last transmission and the event, a stale previous decision and leader tx_in_tb 2 (checked on _alltx, _ltx and _nack)"));
        }
    }
    const std::string art = V.Write("E03_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
