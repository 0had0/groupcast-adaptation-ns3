// E05_traffic_classes (C: CORE; W: SYN_NEAR): the generated traffic follows the class definition; one TB per packet; the PDB used for
// prr_pdb is the class PDB; runs aborted by the one-TB guard are listed (F26).
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <algorithm>
#include <cmath>
#include <dirent.h>
#include <fstream>
#include <map>
#include <set>
#include <sstream>

using namespace ptest;

int
main()
{
    TestReport T("E05", "traffic_classes", "E");
    T.Summary("Packet intervals and sizes follow the traffic classes (periodic1tb 100 ms {190,300}, periodic2tb 10 ms {800,1200}, "
              "aperiodic1tb 50 ms + floor(Exp(50 ms)), sizes 200..1200); one TB per packet; class PDB; one-TB guard aborts (F26).");
    T.AddRef("custom-traffic-application.cc:44-61, 396-452 (class definitions); README traffic classes");
    ViolationLog V;
    T.Info("logged size", Opt().Obs("_pkt.csv 'size' is the wire size of the leader packet (application payload + the 20-byte SeqTsSize header, custom-traffic-application.cc:301-331); "
                                    "t_tx has 0.1 ms resolution (4 printed decimals)"));

    std::map<std::string, std::vector<double>> intervals;   // traffic -> intervals (ms)
    long oneTbBadF32 = 0;
    struct Pk { int size; double t; long seq; };
    std::map<std::string, std::pair<std::string, std::vector<Pk>>> streams;
    std::map<std::string, std::map<int, long>> sizes;       // traffic -> size -> count
    std::map<std::string, long> pktCount;
    long oneTbBad = 0, oneTbN = 0, unq = 0, unqRuns = 0, prrPdbBad = 0, prrPdbN = 0, payloadBad = 0;
    for (const std::string rs : {"CORE", "SYN_NEAR", "SYN_MARGINAL", "SPSDYN"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { if (rs == "CORE") T.Skip("CORE data", "CORE manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        for (const auto& rr : all)
        {
            Run r;
            if (!r.Load(rr)) continue;
            T.AddNRuns(1);
            // arm-independent traffic: use R2 runs only (one per cell/seed)
            // identical (traffic, seed) runs of different cells/arms replay the same application random stream: keep one of them so that the samples are independent
            // runs with the same (traffic, seed) replay the same application random stream (cells differ only in the run length): keep the longest one of each stream
            if (rr.arm == "R2" && !r.pkts[0].empty())
            {
                auto& best = streams[rr.traffic + "/s" + std::to_string(rr.seed)];
                if (r.pkts[0].size() > best.second.size())
                {
                    best.first = rr.traffic;
                    best.second.clear();
                    for (const auto& q : r.pkts[0]) best.second.push_back({q.size, q.tTx, q.seq});
                }
            }
            // one TB per packet
            for (const PTx& x : r.txs)
            {
                if (x.side == 0 && x.txInTb == 1 && r.InWin(x.t))
                {
                    ++oneTbN;
                    if (x.nSeq != 1) { ++(P2fxNoT2(rr) ? oneTbBadF32 : oneTbBad); V.Add("ndi=1 TB carries one packet", rs + "/" + rr.arm + "/" + rr.tag + " slot " + FmtI(x.slot) + " n_seq " + std::to_string(x.nSeq)); }
                }
            }
            const long u = static_cast<long>(r.R("leader_pkt_multi_tb_unqueued", 0));
            unq += u;
            unqRuns += (u > 0);
            if (r.R("tb_max_b", 0) < 1333 || r.R("payload_max_b", 0) + 37 > r.R("tb_max_b", 0)) ++payloadBad;
            // prr_pdb with the class PDB
            for (int m = 1; m <= kMembers; ++m)
            {
                const auto& rows = r.pkts[m - 1];
                long sent = static_cast<long>(rows.size()), inPdb = 0;
                for (const auto& p : rows) inPdb += (p.rx && p.delayMs / 1e3 <= PdbSeconds(rr.traffic) + 1e-9) ? 1 : 0;
                ++prrPdbN;
                if (std::fabs(inPdb / std::max<double>(1, sent) - r.MemberCol("prr_pdb", m)) > 5e-6) ++prrPdbBad;
            }
        }
    }
    for (const auto& [key, st] : streams)
    {
        const auto& rows = st.second;
        for (size_t i = 0; i < rows.size(); ++i)
        {
            sizes[st.first][rows[i].size]++;
            pktCount[st.first]++;
            if (i > 0 && rows[i].seq == rows[i - 1].seq + 1) intervals[st.first].push_back((rows[i].t - rows[i - 1].t) * 1e3);
        }
    }
    if (pktCount.empty()) return T.Finish();
    for (const auto& [traf, iv] : intervals)
    {
        if (iv.size() < 30) continue;
        if (traf.size() > 2 && traf.compare(traf.size() - 2, 2, "fx") == 0)
        {
            // TEST_PLAN 0.3 (3): fixed-size leader classes: every packet has the class size, arrivals as the base class
            const int want = traf == "periodic1fx" ? 300 : 1200;
            const double per = traf.rfind("periodic1", 0) == 0 ? 100.0 : (traf.rfind("periodic2", 0) == 0 ? 10.0 : -1.0);
            long tot = pktCount[traf], nWant = sizes[traf][want];
            T.Check(traf + ": every packet has the fixed size " + std::to_string(want) + " B", tot > 0 && nWant == tot, Opt().Exp(FmtI(tot) + " x " + std::to_string(want)).Obs(FmtI(nWant) + " of " + FmtI(tot)).Tol("0").Sev("M")
                        .Note("TEST_PLAN 0.3 (3): CustomTrafficApplication FixedSize on the leader only"));
            if (per > 0)
            {
                double mx = 0;
                for (double d : iv) mx = std::max(mx, std::fabs(d - per));
                T.Check(traf + ": interval " + Fmt(per, 3) + " ms", mx <= 0.15, Opt().Exp(Fmt(per, 3) + " ms").Obs("max deviation " + Fmt(mx, 4) + " ms over " + FmtI(static_cast<long>(iv.size()))).Tol("0.15 ms").Sev("M"));
            }
            continue;
        }
        if (traf == "periodic1tb")
        {
            double mx = 0;
            for (double d : iv) mx = std::max(mx, std::fabs(d - 100.0));
            T.Check("periodic1tb: interval 100 ms", mx <= 0.15, Opt().Exp("100 ms").Obs("max deviation " + Fmt(mx, 4) + " ms over " + FmtI(static_cast<long>(iv.size())) + " intervals").Tol("0.15 ms (t_tx printed with 0.1 ms resolution; the plan's 1 us is not observable)").Sev("M"));
            long n300 = sizes[traf][300], n190 = sizes[traf][190], tot = pktCount[traf];
            const Prop p = Wilson(n300, tot);
            T.Check("periodic1tb: sizes in {190, 300} with the 300 share 0.2", n300 + n190 == tot && std::fabs(p.p - 0.2) <= 0.02,
                    Opt().Exp("share 0.2 +- 0.02").Obs(FmtI(n300) + " x 300, " + FmtI(n190) + " x 190 of " + FmtI(tot) + " (share " + Fmt(p.p, 4) + ")").Tol("0.02").Sev("M"));
        }
        else if (traf == "periodic2tb")
        {
            double mx = 0;
            for (double d : iv) mx = std::max(mx, std::fabs(d - 10.0));
            T.Check("periodic2tb: interval 10 ms", mx <= 0.15, Opt().Exp("10 ms").Obs("max deviation " + Fmt(mx, 4) + " ms over " + FmtI(static_cast<long>(iv.size()))).Tol("0.15 ms (print resolution)").Sev("M"));
            long n12 = sizes[traf][1200], n8 = sizes[traf][800], tot = pktCount[traf];
            const Prop p = Wilson(n12, tot);
            T.Check("periodic2tb: sizes in {800, 1200} with P(1200) = 0.2 (Wilson 95%)", n12 + n8 == tot && p.lo <= 0.2 && 0.2 <= p.hi,
                    Opt().Exp("0.2 inside the CI").Obs(Fmt(p.p, 4) + " [" + Fmt(p.lo, 4) + ", " + Fmt(p.hi, 4) + "] over " + FmtI(tot)).Sev("M"));
        }
        else
        {
            // interval - 50 ms ~ floor(Exp(mean 50 ms))
            std::vector<double> ex;
            for (double d : iv) ex.push_back(d - 50.0);
            const MeanCI m = MeanCi(ex);
            const double want = 49.5;
            T.Check("aperiodic1tb: mean(interval - 50 ms) = 49.5 ms (floor(Exp(50)))", m.ok() && m.lo <= want && want <= m.hi,
                    Opt().Exp("49.5 inside the 95% CI").Obs(FmtCi(m.mean, m.lo, m.hi, 4)).Sev("M"));
            // discrete data (1 ms steps): chi-square on 10 ms bins of floor(interval - 50 ms) against floor(Exp(mean 50 ms)); the tail beyond 200 ms is one bin
            std::vector<double> ob(21, 0), pb(21, 0);
            for (double e : ex)
            {
                const int k = std::min(20, std::max(0, static_cast<int>(std::floor(e + 0.5) / 10.0)));
                ob[k] += 1;
            }
            for (int k = 0; k < 20; ++k) pb[k] = std::exp(-10.0 * k / 50.0) - std::exp(-10.0 * (k + 1) / 50.0);
            pb[20] = std::exp(-200.0 / 50.0);
            const Gof chi = ChiSqGof(ob, pb);
            T.Check("aperiodic1tb: chi-square of the 10 ms-binned (interval - 50 ms) against floor(Exp(50 ms)) (p > 0.01)", chi.p > 0.01,
                    Opt().Exp("p > 0.01").Obs("chi2=" + Fmt(chi.stat, 4) + " df=" + FmtI(chi.df) + " p=" + Fmt(chi.p, 4) + " n=" + FmtI(static_cast<long>(ex.size()))).Sev("M")
                        .Note("replaces the KS test of the plan: the intervals are discrete (1 ms) and t_tx has 0.1 ms resolution, which makes a KS test on ties anti-conservative"));
            std::vector<double> obs(6, 0), prob(6, 1.0 / 6);
            long tot = 0;
            for (int s = 200; s <= 1200; s += 200) { obs[s / 200 - 1] = static_cast<double>(sizes[traf][s]); tot += sizes[traf][s]; }
            const Gof ch = ChiSqGof(obs, prob);
            T.Check("aperiodic1tb: sizes uniform on {200,...,1200} (chi-square p > 0.01)", tot == pktCount[traf] && ch.p > 0.01,
                    Opt().Exp("p > 0.01").Obs("chi2=" + Fmt(ch.stat, 4) + " p=" + Fmt(ch.p, 4) + " n=" + FmtI(tot) + " of " + FmtI(pktCount[traf])).Sev("M"));
        }
    }
    if (oneTbBadF32 > 0)
        T.Fail("one TB per packet, periodic2fx without --t2Ms: ndi=1 leader transmissions carrying more than one packet", Opt().Fnd("F32").Sev("M").Exp("0").Obs(FmtI(oneTbBadF32)).Tol("0").Note("F32 (TEST_PLAN 0.4): periodic2fx without --t2Ms. period = RRI = PDB = 10 ms = 20 slots and the selection window is the full PDB counted from the scheduling slot (F23), so TB k's last reserved transmission can land after packet k+1 is generated; the SB-SPS grant keeps its HARQ process and the MAC sends the new packet in TB k's retransmission slot (one packet counted in two TBs, backlog, one-TB guard). Runs with --t2Ms=9 (T2 = 18 slots) must show 0"));
    T.Check("one TB per packet: every ndi=1 leader transmission carries exactly one packet", oneTbN > 0 && oneTbBad == 0, Opt().Exp("0 of " + FmtI(oneTbN)).Obs(FmtI(oneTbBad)).Tol("0").Sev("H"));
    T.Check("payload + 37 B leader overhead <= tb_max_b (1333 B) in every run", payloadBad == 0, Opt().Exp("0").Obs(FmtI(payloadBad)).Tol("0").Sev("M"));
    T.Check("prr_pdb recomputed with the class PDB (100 / 10 / 50 ms) equals _member.csv", prrPdbBad == 0, Opt().Exp("0 of " + FmtI(prrPdbN)).Obs(FmtI(prrPdbBad)).Tol("5e-6").Sev("H"));
    T.Info("leader_pkt_multi_tb_unqueued (packets that did not fit their own TB), all runs", Opt().Obs(FmtI(unq) + " in " + FmtI(unqRuns) + " runs").Fnd("F26"));
    // aborted runs of the one-TB guard
    {
        long aborted = 0, guard = 0, total = 0;
        std::string ex;
        DIR* d = opendir(OutDir().c_str());
        if (d)
        {
            while (dirent* e = readdir(d))
            {
                const std::string nm = e->d_name;
                if (nm[0] == '.' ) continue;
                std::ifstream f(OutDir() + "/" + nm + "/failed.txt");
                std::string line;
                while (std::getline(f, line))
                {
                    if (line.empty()) continue;
                    ++total;
                    const std::string arm_tag = line.substr(0, line.find(':'));
                    std::ifstream lf(OutDir() + "/" + nm + "/" + arm_tag + ".log");
                    std::stringstream ss;
                    ss << lf.rdbuf();
                    const bool g = ss.str().find("did not fit their own TB") != std::string::npos;
                    ++aborted;
                    guard += g;
                    if (ex.size() < 400) ex += nm + "/" + arm_tag + (g ? "(guard) " : "(other) ");
                }
            }
            closedir(d);
        }
        if (aborted > 0)
            T.Xfail("F26: runs aborted by the one-TB guard (numBg == 0, packets that do not fit their own TB)", Opt().Fnd("F26").Sev("M").Exp("0 aborted runs")
                        .Obs(FmtI(guard) + " of " + FmtI(aborted) + " failed runs ended in the guard abort (platoon_harq_robustness_eval.cc:2760-2763): " + ex)
                        .Note("the guard aborts only when numBg == 0; with background the same condition only increments leader_pkt_multi_tb_unqueued, so the invariant is silently violated there"));
        else
            T.Pass("F26: no run aborted by the one-TB guard", Opt().Fnd("F26"));
    }
    const std::string art = V.Write("E05_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
