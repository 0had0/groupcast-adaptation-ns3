// C06_cross_source_combining (U + W: SYN_COMB): does a member soft-combine the helper's relayed copy of a leader TB with
// the leader's own copy (cooperative HARQ: the copy's SCI-2A carries the leader's HARQ id / source and
// coop.relayedFromRnti, NrSpectrumPhy keys the HARQ buffer on relayedFromRnti, NrEesmIrT1 combines)?
//
// U: NrEesmIrT1 evaluated directly at the SINR points of the run-set (deterministic): each copy alone fails, the
//    two combined decode; and the EESM-IR averaging property (a weak earlier copy lowers a strong copy's effective SINR).
// W: SYN_COMB (campaign/runsets.py COMB_CASES): 3 platoon nodes L = 0, H = 1 (static helper), J = 2, PSSCH SINR of every
//    link fixed by --testFixedSinr, members' HARQ buffer events from --writeHarqTrace (_harq.csv, NrSpectrumPhy trace
//    SlHarqCombine).  Per case: SINR / TBLER of each copy at J, buffer length when the second copy is decoded, the
//    outcome, the relayed copy's SCI-2A, no buffer reset between the copies, HARQ-id reuse before a copy.
//
//   C06 --calibrate <tbBytes> <nRb>      TBLER table (alone / 2 / 3 copies combined), MCS 14
//   C06 --seq <tbBytes> <nRb> s0 s1 ...   per-copy TBLER and effective SINR of successive copies of one TB
#include "common/csv.h"
#include "common/harness.h"
#include "common/runsets.h"

#include "ns3/core-module.h"
#include "ns3/nr-eesm-ir-t1.h"
#include "ns3/spectrum-model.h"
#include "ns3/spectrum-value.h"

#include <cmath>
#include <cstdio>
#include <map>
#include <numeric>
#include <string>
#include <vector>

using namespace ns3;
using namespace ptest;

namespace
{

constexpr uint8_t kMcs = 14;
constexpr int kJ = 2; //!< receiver J (member / node 2)
constexpr int kH = 1; //!< helper H (member / node 1)

/// TBLER and effective SINR (dB) of each successive copy of one TB under NrEesmIrT1 (copy k combined with copies
/// 0..k-1, as NrSpectrumPhy does for one HARQ process), every RB of copy k at sinrDb[k]
std::vector<std::pair<double, double>>
EesmIr(const std::vector<double>& sinrDb, uint32_t tbBytes, uint32_t nRb, uint8_t mcs)
{
    std::vector<double> f(nRb);
    std::iota(f.begin(), f.end(), 1.0);
    Ptr<SpectrumModel> sm = Create<SpectrumModel>(f);
    std::vector<int> map(nRb);
    std::iota(map.begin(), map.end(), 0);
    Ptr<NrEesmIrT1> em = CreateObject<NrEesmIrT1>();
    NrErrorModel::NrErrorModelHistory hist;
    std::vector<std::pair<double, double>> out;
    for (double db : sinrDb)
    {
        SpectrumValue v(sm);
        v = std::pow(10.0, db / 10.0);
        Ptr<NrErrorModelOutput> o = em->GetTbDecodificationStats(v, map, tbBytes, mcs, hist);
        auto e = DynamicCast<NrEesmErrorModelOutput>(o);
        out.emplace_back(o->m_tbler, 10.0 * std::log10(e->m_sinrEff));
        hist.push_back(o);
    }
    return out;
}

/// "--testFixedSinr=rx:tx=dB;..." of a manifest command -> (rx, tx) -> dB
std::map<std::pair<int, int>, double>
FixedSinrOf(const std::string& cmd)
{
    std::map<std::pair<int, int>, double> m;
    const std::string key = "--testFixedSinr=";
    auto p = cmd.find(key);
    if (p == std::string::npos)
    {
        return m;
    }
    p += key.size();
    auto e = cmd.find(' ', p);
    std::string v = cmd.substr(p, e == std::string::npos ? std::string::npos : e - p);
    std::string clean;
    for (char c : v)
    {
        if (c != '\'' && c != '"')
        {
            clean += c;
        }
    }
    size_t s = 0;
    while (s < clean.size())
    {
        auto semi = clean.find(';', s);
        std::string item = clean.substr(s, semi == std::string::npos ? std::string::npos : semi - s);
        int rx = 0, tx = 0;
        double db = 0;
        if (std::sscanf(item.c_str(), "%d:%d=%lf", &rx, &tx, &db) == 3)
        {
            m[{rx, tx}] = db;
        }
        if (semi == std::string::npos)
        {
            break;
        }
        s = semi + 1;
    }
    return m;
}

enum Ev
{
    DECODE = 0,
    REMOVE_PREV_NDI = 1,
    SKIP_PREV_DECODED = 2,
    RESET_NO_COMBINE = 3,
    RESET_NDI = 4,
    RESET_DECODED = 5,
    SCI2_FAIL = 6
};

/// one _harq.csv row (members' HARQ buffer event), with the leader TB it belongs to (from _att.csv)
struct HEv
{
    double tMs;
    int member, txNode, txRnti, harqRnti, relayedFrom, harqId, ndi, rv, mcs, nRb, event, hist, corrupt;
    long src, tbSize;
    double sinrDb, effDb, tbler;
    int64_t tb{-1};
};

/// per-case aggregate over the seeds (the run-set's expectations are per case)
struct CaseAgg
{
    long runs{0};
    // L -> H
    long hDec{0}, hFail{0}, hSci2Fail{0};
    // J: first copy = the leader's NDI = 1 copy of a TB in the KPI window; second = the next decode attempt of that TB
    long tbs{0}, firstCorrupt{0}, second{0}, secondFromHelper{0}, secondOk{0}, secondHist1{0}, secondHist0{0};
    long resetBetween{0}, noCombineReset{0}, decodesJ{0}, decodesJHist0{0};
    long third{0}, thirdOk{0};
    double sinrL{NAN}, sinrH{NAN}, effSecond{0};
    double tblerLmin{2}, tblerLmax{-1}, tblerHmin{2}, tblerHmax{-1};
    // SCI-2A of the relayed copy vs the leader's copy of the same TB
    long sciChecked{0}, sciBad{0};
    std::string sciEx;
    // HARQ-id reuse: a copy of TB k at J after the leader's NDI = 1 of a newer TB on the same HARQ id
    long copiesJ{0}, reuse{0};
    double maxLagMs{0};
    double prrJ{NAN};
    long tbSize{0}, nRb{0};
    std::map<std::pair<int, int>, double> cfg;
};

bool
LoadEvents(const RunRef& r, std::vector<HEv>& ev, double& k0, double& k1)
{
    Table h, a, run;
    if (!LoadTable(r.prefix + "_harq.csv", h) || !LoadTable(r.prefix + "_att.csv", a) ||
        !LoadTable(r.prefix + "_run.csv", run))
    {
        return false;
    }
    k0 = run.R0("kpi_start") * 1000.0;
    k1 = run.R0("kpi_end") * 1000.0;
    // (side, slot) -> TB (the _att rows of a transmission carry the leader TB it belongs to)
    std::map<std::pair<int, int64_t>, int64_t> tbOf;
    const auto& side = a.D("side");
    const auto& slot = a.D("slot_abs");
    const auto& tb = a.D("tb");
    for (size_t i = 0; i < a.nrows; ++i)
    {
        tbOf[{static_cast<int>(side[i]), static_cast<int64_t>(slot[i])}] = static_cast<int64_t>(tb[i]);
    }
    for (size_t i = 0; i < h.nrows; ++i)
    {
        HEv e;
        e.tMs = h.At("t_ms", i);
        e.member = static_cast<int>(h.At("member", i));
        e.txNode = static_cast<int>(h.At("tx_node", i));
        e.txRnti = static_cast<int>(h.At("tx_rnti", i));
        e.harqRnti = static_cast<int>(h.At("harq_rnti", i));
        e.relayedFrom = static_cast<int>(h.At("relayed_from_rnti", i));
        e.harqId = static_cast<int>(h.At("harq_id", i));
        e.ndi = static_cast<int>(h.At("ndi", i));
        e.rv = static_cast<int>(h.At("rv", i));
        e.mcs = static_cast<int>(h.At("mcs", i));
        e.src = static_cast<long>(h.At("src_l2", i));
        e.tbSize = static_cast<long>(h.At("tb_size", i));
        e.nRb = static_cast<int>(h.At("n_rb", i));
        e.event = static_cast<int>(h.At("event", i));
        e.hist = static_cast<int>(h.At("hist_before", i));
        e.sinrDb = h.At("sinr_db", i);
        e.effDb = h.At("sinr_eff_db", i);
        e.tbler = h.At("tbler", i);
        e.corrupt = static_cast<int>(h.At("corrupt", i));
        const int s = (e.txNode == 0) ? 0 : 1;
        auto it = tbOf.find({s, static_cast<int64_t>(std::floor(e.tMs / 0.5))});
        e.tb = (it != tbOf.end()) ? it->second : -1;
        ev.push_back(e);
    }
    return true;
}

void
Analyse(const RunRef& r, CaseAgg& A)
{
    std::vector<HEv> ev;
    double k0 = 0, k1 = 0;
    if (!LoadEvents(r, ev, k0, k1))
    {
        return;
    }
    A.runs++;
    A.cfg = FixedSinrOf(r.cmd);
    Table mem;
    if (LoadTable(r.prefix + "_member.csv", mem))
    {
        for (size_t i = 0; i < mem.nrows; ++i)
        {
            if (mem.StrAt("member", i) == std::to_string(kJ))
            {
                A.prrJ = mem.At("prr", i);
            }
        }
    }
    // TBs whose first leader copy lies in the KPI window
    std::map<int64_t, double> tbStart;
    for (const HEv& e : ev)
    {
        if (e.txNode == 0 && e.ndi == 1 && e.tb >= 0 && !tbStart.count(e.tb))
        {
            tbStart[e.tb] = e.tMs;
        }
    }
    auto inWin = [&](int64_t tb) {
        auto it = tbStart.find(tb);
        return it != tbStart.end() && it->second >= k0 && it->second < k1;
    };
    // L -> H
    for (const HEv& e : ev)
    {
        if (e.member == kH && e.txNode == 0 && inWin(e.tb))
        {
            if (e.event == DECODE)
            {
                A.hDec++;
                A.hFail += e.corrupt;
            }
            else if (e.event == SCI2_FAIL)
            {
                A.hSci2Fail++;
            }
        }
    }
    // J: per TB, the ordered events
    std::map<int64_t, std::vector<size_t>> byTb;
    std::map<int64_t, int> leaderHarq; // TB -> leader's HARQ id (key of J's buffer)
    for (size_t i = 0; i < ev.size(); ++i)
    {
        const HEv& e = ev[i];
        if (e.member != kJ)
        {
            continue;
        }
        if (e.event == DECODE)
        {
            A.decodesJ++;
            A.decodesJHist0 += (e.hist == 0);
            if (e.txNode == 0)
            {
                A.sinrL = e.sinrDb;
                A.tblerLmin = std::min(A.tblerLmin, e.tbler);
                A.tblerLmax = std::max(A.tblerLmax, e.tbler);
            }
            else
            {
                A.sinrH = e.sinrDb;
                A.tblerHmin = std::min(A.tblerHmin, e.tbler);
                A.tblerHmax = std::max(A.tblerHmax, e.tbler);
            }
            A.tbSize = e.tbSize;
            A.nRb = e.nRb;
        }
        if (e.event == RESET_NO_COMBINE)
        {
            A.noCombineReset++;
        }
        if (e.tb >= 0)
        {
            byTb[e.tb].push_back(i);
        }
    }
    // HARQ-id reuse: walk J's events in order; the latest NDI = 1 leader TB per HARQ id
    std::map<int, int64_t> latestNew;
    for (const HEv& e : ev)
    {
        if (e.member != kJ || e.tb < 0)
        {
            continue;
        }
        if (e.txNode == 0 && e.ndi == 1)
        {
            latestNew[e.harqId] = std::max(latestNew[e.harqId], e.tb);
        }
        if (e.txNode != 0 && (e.event == DECODE || e.event == SKIP_PREV_DECODED || e.event == SCI2_FAIL))
        {
            A.copiesJ++;
            auto it = latestNew.find(e.harqId);
            A.reuse += (it != latestNew.end() && it->second > e.tb);
            if (tbStart.count(e.tb))
            {
                A.maxLagMs = std::max(A.maxLagMs, e.tMs - tbStart[e.tb]);
            }
        }
    }
    for (const auto& [tb, idx] : byTb)
    {
        if (!inWin(tb))
        {
            continue;
        }
        // first decode attempt = the leader's NDI = 1 copy
        size_t f = SIZE_MAX;
        for (size_t k : idx)
        {
            if (ev[k].event == DECODE && ev[k].txNode == 0 && ev[k].ndi == 1)
            {
                f = k;
                break;
            }
        }
        if (f == SIZE_MAX)
        {
            continue;
        }
        A.tbs++;
        if (!ev[f].corrupt)
        {
            continue;
        }
        A.firstCorrupt++;
        const HEv& L = ev[f];
        // the next decode attempt of this TB at J; buffer events between the two on the same process
        size_t s = SIZE_MAX;
        bool reset = false;
        for (size_t k : idx)
        {
            if (k <= f)
            {
                continue;
            }
            const HEv& e = ev[k];
            if (e.event == DECODE)
            {
                s = k;
                break;
            }
            if (e.event == REMOVE_PREV_NDI || e.event == RESET_NDI || e.event == RESET_DECODED)
            {
                reset = true;
            }
        }
        // a reset of the same process by another TB (same HARQ id) between the two copies
        if (s != SIZE_MAX)
        {
            for (size_t k = f + 1; k < s; ++k)
            {
                const HEv& e = ev[k];
                if (e.member == kJ && e.harqRnti == L.harqRnti && e.harqId == L.harqId &&
                    (e.event == REMOVE_PREV_NDI || e.event == RESET_NDI || e.event == RESET_DECODED))
                {
                    reset = true;
                }
            }
        }
        if (s == SIZE_MAX)
        {
            continue;
        }
        const HEv& S = ev[s];
        A.second++;
        A.secondFromHelper += (S.txNode != 0);
        A.secondOk += (S.corrupt == 0);
        A.secondHist1 += (S.hist == 1);
        A.secondHist0 += (S.hist == 0);
        A.resetBetween += reset;
        A.effSecond += S.effDb;
        // a third attempt (e.g. the helper's second copy)
        for (size_t k : idx)
        {
            if (k > s && ev[k].event == DECODE)
            {
                A.third++;
                A.thirdOk += (ev[k].corrupt == 0);
                break;
            }
        }
        // SCI-2A of a relayed copy vs the leader's copy of the same TB
        if (S.txNode != 0)
        {
            A.sciChecked++;
            std::string bad;
            if (S.harqId != L.harqId) bad += " harqId " + std::to_string(S.harqId) + "!=" + std::to_string(L.harqId);
            if (S.src != L.src) bad += " srcL2 " + std::to_string(S.src) + "!=" + std::to_string(L.src);
            if (S.ndi != 0) bad += " ndi " + std::to_string(S.ndi);
            if (S.rv != 2) bad += " rv " + std::to_string(S.rv);
            if (S.tbSize != L.tbSize) bad += " tbSize " + std::to_string(S.tbSize) + "!=" + std::to_string(L.tbSize);
            if (S.mcs != L.mcs || S.nRb != L.nRb) bad += " mcs/nRb";
            if (S.relayedFrom != L.txRnti || S.harqRnti != L.txRnti) bad += " relayedFrom/harqRnti";
            if (!bad.empty())
            {
                A.sciBad++;
                if (A.sciEx.size() < 200)
                {
                    A.sciEx += r.arm + "/s" + std::to_string(r.seed) + " t=" + Fmt(S.tMs, 3) + bad + "; ";
                }
            }
        }
    }
}

std::string
P(long k, long n)
{
    return FmtI(k) + "/" + FmtI(n) + (n > 0 ? " (" + Fmt(static_cast<double>(k) / n, 3) + ")" : "");
}

} // namespace

int
main(int argc, char** argv)
{
    if (argc >= 4 && std::string(argv[1]) == "--calibrate")
    {
        const uint32_t tb = std::stoul(argv[2]);
        const uint32_t nRb = std::stoul(argv[3]);
        std::printf("TB %u B, %u RB, MCS %u\n  sinr  alone  comb(s,s)  comb(s,s,s)\n", tb, nRb, kMcs);
        for (double s = -4.0; s <= 16.0; s += 0.5)
        {
            auto a = EesmIr({s}, tb, nRb, kMcs);
            auto c = EesmIr({s, s, s}, tb, nRb, kMcs);
            std::printf("%6.1f  %.4f  %.4f  %.4f\n", s, a[0].first, c[1].first, c[2].first);
        }
        return 0;
    }
    if (argc >= 5 && std::string(argv[1]) == "--seq")
    {
        std::vector<double> v;
        for (int i = 4; i < argc; ++i)
        {
            v.push_back(std::stod(argv[i]));
        }
        auto r = EesmIr(v, std::stoul(argv[2]), std::stoul(argv[3]), kMcs);
        auto sci2 = EesmIr({v[0]}, 5, std::stoul(argv[3]), 0);
        for (size_t k = 0; k < r.size(); ++k)
        {
            std::printf("copy %zu at %.1f dB: TBLER %.4f, effective SINR %.2f dB\n", k, v[k], r[k].first, r[k].second);
        }
        std::printf("SCI-2A (5 B, MCS 0) at %.1f dB: TBLER %.4f\n", v[0], sci2[0].first);
        return 0;
    }

    TestReport T("C06", "cross_source_combining", "C");
    T.Summary("A member soft-combines the helper's relayed copy with the leader's copy of the same TB (same HARQ buffer, "
              "SCI-2A carries the leader's HARQ id / source, NDI 0); NrEesmIrT1 combining is an EESM average of the copies, "
              "so a weak earlier copy can make a strong copy fail (F35).");
    T.AddRef("TS 38.321 5.22.1.3 / 5.22.2.2 (SL HARQ process, soft combining of the TB's transmissions)");
    T.AddRef("nr-spectrum-phy.cc (HARQ owner = coop.relayedFromRnti), nr-eesm-ir.cc ComputeSINR (IR: SINR_eff = EESM over all copies' RBs, Reff from the summed code bits)");
    T.AddRunset("SYN_COMB");

    // ---------------- U: the error model at the run-set's SINR points (TB of periodic1fx: 335 B on 20 RB)
    uint32_t tbB = 335, nRb = 20;
    const auto all = LoadManifest("SYN_COMB");
    {
        Table h;
        for (const auto& r : all)
        {
            if (r.traffic == "periodic1fx" && LoadTable(r.prefix + "_harq.csv", h) && h.nrows > 0)
            {
                tbB = static_cast<uint32_t>(h.At("tb_size", 0));
                nRb = static_cast<uint32_t>(h.At("n_rb", 0));
                break;
            }
        }
    }
    {
        auto aL = EesmIr({7.8}, tbB, nRb, kMcs)[0].first;
        auto aH = EesmIr({8.2}, tbB, nRb, kMcs)[0].first;
        auto c = EesmIr({7.8, 8.2}, tbB, nRb, kMcs);
        auto cc = EesmIr({7.8, 7.8}, tbB, nRb, kMcs);
        T.Check("model: each copy alone fails, leader + helper combined decodes (NrEesmIrT1, " + FmtI(tbB) + " B, " +
                    FmtI(nRb) + " RB, MCS 14)",
                aL >= 0.999 && aH >= 0.999 && c[1].first <= 0.001 && cc[1].first <= 0.001,
                Opt().Exp("TBLER alone(7.8 dB), alone(8.2 dB) >= 0.999; combined(7.8 -> 8.2), combined(7.8 -> 7.8) <= 0.001")
                    .Obs("alone " + Fmt(aL, 4) + " / " + Fmt(aH, 4) + "; combined " + Fmt(c[1].first, 4) + " (effective " +
                         Fmt(c[1].second, 2) + " dB) / " + Fmt(cc[1].first, 4))
                    .Sev("H").Note("validates the run-set design: the W checks below are then deterministic"));
        auto s10 = EesmIr({10.0}, tbB, nRb, kMcs)[0].first;
        auto w = EesmIr({0.0, 10.0}, tbB, nRb, kMcs);
        auto w3 = EesmIr({0.0, 10.0, 10.0}, tbB, nRb, kMcs);
        T.CheckXfail("model: combining never makes a decodable copy fail (strong 10 dB copy after a failed 0 dB copy)",
                     w[1].first <= s10 + 1e-3,
                     Opt().Fnd("F35").Sev("M")
                         .Exp("TBLER(0 -> 10 dB combined) <= TBLER(10 dB alone)")
                         .Obs("alone " + Fmt(s10, 4) + ", combined " + Fmt(w[1].first, 4) + " (effective " + Fmt(w[1].second, 2) +
                              " dB); with a second 10 dB copy " + Fmt(w3[2].first, 4) + " (" + Fmt(w3[2].second, 2) + " dB)")
                         .Note("F35: NrEesmIr::ComputeSINR takes SINR_eff = -beta ln(mean over the RBs of ALL copies of "
                               "exp(-SINR/beta)) and maps it on the BLER curve of the lowest MCS of the same modulation "
                               "(GetMcsEq). The exponential mean is dominated by the weakest copy (it tends to SINR_weak + "
                               "beta ln 2 when the other copy is strong), so a weak earlier copy pulls a strong copy below "
                               "its own threshold. Real IR (LLR combining) is never worse than the best copy. It lowers the "
                               "gain of combining exactly where helper copies are strong and leader copies weak."));
        // how often the model is non-monotone on a grid (weak first copy x strong second copy)
        long nonMono = 0, cells = 0;
        std::string ex;
        for (double sw : {-6.0, -3.0, 0.0, 2.0, 4.0, 6.0})
        {
            for (double ss : {9.5, 10.0, 12.0, 15.0, 20.0, 30.0})
            {
                const double alone = EesmIr({ss}, tbB, nRb, kMcs)[0].first;
                const double comb = EesmIr({sw, ss}, tbB, nRb, kMcs)[1].first;
                ++cells;
                if (comb > alone + 0.01)
                {
                    ++nonMono;
                    if (ex.size() < 300)
                    {
                        char buf[96];
                        std::snprintf(buf, sizeof(buf), "(%.0f dB then %.1f dB: %.3f -> %.3f) ", sw, ss, alone, comb);
                        ex += buf;
                    }
                }
            }
        }
        T.Info("model: (weak first, strong second) grid points where combining raises the strong copy's TBLER",
               Opt().Obs(FmtI(nonMono) + " of " + FmtI(cells) + " points; " + ex).Note("F35 extent"));
        T.Metric("model_nonmonotone_points", nonMono);
    }

    // ---------------- W: SYN_COMB
    if (all.empty())
    {
        T.Skip("SYN_COMB runs", "no SYN_COMB manifest: python3 campaign/run_matrix.py --runset SYN_COMB");
        return T.Finish();
    }
    std::map<std::string, CaseAgg> agg;
    for (const auto& r : all)
    {
        Analyse(r, agg[r.arm]);
        T.AddNRuns(1);
    }
    CsvWriter cw(ResultsDir() + "/C06_copies.csv",
                 "case,runs,tb_bytes,n_rb,sinr_J_from_L_db,sinr_J_from_H_db,tbler_L_min,tbler_L_max,tbler_H_min,tbler_H_max,"
                 "tbs,first_corrupt,second,second_from_helper,second_hist1,second_ok,second_eff_db_mean,third,third_ok,"
                 "reset_between,no_combine_resets,decodes,prr_J");
    for (const auto& [name, A] : agg)
    {
        cw.Row({name, FmtI(A.runs), FmtI(A.tbSize), FmtI(A.nRb), Fmt(A.sinrL, 2), Fmt(A.sinrH, 2), Fmt(A.tblerLmin, 4),
                Fmt(A.tblerLmax, 4), Fmt(A.tblerHmin, 4), Fmt(A.tblerHmax, 4), FmtI(A.tbs), FmtI(A.firstCorrupt), FmtI(A.second),
                FmtI(A.secondFromHelper), FmtI(A.secondHist1), FmtI(A.secondOk),
                Fmt(A.second ? A.effSecond / A.second : NAN, 2), FmtI(A.third), FmtI(A.thirdOk), FmtI(A.resetBetween),
                FmtI(A.noCombineReset), FmtI(A.decodesJ), Fmt(A.prrJ, 3)});
    }
    T.Artefact("results/C06_copies.csv");

    for (auto& [name, A] : agg)
    {
        const std::string pre = "SYN_COMB " + name + ": ";
        const bool helperArm = name.rfind("R3", 0) != 0;
        const bool combine = name.find("sc1") != std::string::npos || name.find("bsc1") != std::string::npos;
        const bool noCombine = name.find("sc0") != std::string::npos || name.find("bsc0") != std::string::npos;
        const bool weak = name.find("weak") != std::string::npos;
        const bool reuseCase = name.find("reuse") != std::string::npos;
        if (A.runs == 0)
        {
            T.Skip(pre + "runs", "no complete run");
            continue;
        }
        T.Check(pre + "L -> H decodes every leader copy", A.hDec > 0 && A.hFail == 0 && A.hSci2Fail == 0,
                Opt().Exp("0 failures").Obs(FmtI(A.hFail) + " corrupt + " + FmtI(A.hSci2Fail) + " SCI-2A failures in " + FmtI(A.hDec) + " decodes")
                    .Tol("0").Sev("H"));
        const double cfgL = A.cfg.count({kJ, 0}) ? A.cfg[{kJ, 0}] : NAN;
        const double cfgH = A.cfg.count({kJ, kH}) ? A.cfg[{kJ, kH}] : NAN;
        const bool sinrOk = std::fabs(A.sinrL - cfgL) < 0.01 && (!helperArm || std::isnan(A.sinrH) || std::fabs(A.sinrH - cfgH) < 0.01);
        std::string obs = "J <- L " + Fmt(A.sinrL, 4) + " dB (TBLER of its decodes " + Fmt(A.tblerLmin, 4) + ".." + Fmt(A.tblerLmax, 4) + ")";
        if (helperArm)
        {
            obs += ", J <- H " + Fmt(A.sinrH, 4) + " dB (TBLER of its decodes " + Fmt(A.tblerHmin, 4) + ".." + Fmt(A.tblerHmax, 4) + ")";
        }
        T.Check(pre + "PHY SINR of each copy at J = the fixed value (J <- L " + Fmt(cfgL, 3) + " dB" + (helperArm ? ", J <- H " + Fmt(cfgH, 3) + " dB)" : ")"), sinrOk,
                Opt().Exp("|PHY - configured| < 0.01 dB").Obs(obs).Tol("0.01 dB").Sev("H")
                    .Note("TBLER = the error model's output for that copy, combined with the earlier copies in the buffer"));
        if (reuseCase)
        {
            T.Check(pre + "no copy of TB k reaches J after the leader's NDI = 1 of a newer TB on the same HARQ id", A.copiesJ > 0 && A.reuse == 0,
                    Opt().Exp("0 (otherwise the copy would be combined with, or skipped as, the newer TB: NrSpectrumPhy keys the buffer on (relayedFromRnti, HARQ id) only)")
                        .Obs(FmtI(A.reuse) + " of " + FmtI(A.copiesJ) + " copies at J; latest copy " + Fmt(A.maxLagMs, 1) + " ms after the TB's initial (RRI 10 ms)")
                        .Tol("0").Sev("M")
                        .Note("periodic2fx, SPS leader (one HARQ id for every TB), J decodes no copy so the helper uses every handed slot. The selection window "
                              "(T2 <= PDB <= RRI) keeps every copy of TB k before TB k+1's initial; the campaign data agrees (harq_helper_study/combining_evidence.py check 3: "
                              "0 of 447 k helper copies of R4..R6 after a newer TB on the same HARQ id)."));
            T.Metric(name + "_reuse", A.reuse);
            continue;
        }
        if (A.firstCorrupt == 0)
        {
            T.Fail(pre + "the leader's copy at J fails", Opt().Obs("no corrupt first copy in " + FmtI(A.tbs) + " TBs"));
            continue;
        }
        T.Check(pre + "the leader's NDI = 1 copy fails at J alone", A.firstCorrupt == A.tbs,
                Opt().Exp(FmtI(A.tbs) + " of " + FmtI(A.tbs)).Obs(P(A.firstCorrupt, A.tbs)).Tol("0").Sev("H"));
        if (helperArm)
        {
            T.Check(pre + "the next decode attempt of the TB at J is the helper's copy", A.second > 0 && A.secondFromHelper == A.second,
                    Opt().Exp("all").Obs(P(A.secondFromHelper, A.second)).Tol("0").Sev("H"));
            T.Check(pre + "relayed copy's SCI-2A: leader's HARQ id and source L2 id, NDI 0, RV 2, same TB size / MCS / RBs, relayedFromRnti = HARQ owner = leader",
                    A.sciChecked > 0 && A.sciBad == 0,
                    Opt().Exp("0 mismatches").Obs(FmtI(A.sciBad) + " of " + FmtI(A.sciChecked) + (A.sciEx.empty() ? "" : ": " + A.sciEx)).Tol("0").Sev("H"));
        }
        if (combine)
        {
            T.Check(pre + "J's buffer is not reset between the leader's and the next copy (no RemovePrevDecoded / ResetSlDataHarqProcessStatus on the process)",
                    A.resetBetween == 0 && A.noCombineReset == 0,
                    Opt().Exp("0").Obs(FmtI(A.resetBetween) + " TBs with a reset between, " + FmtI(A.noCombineReset) + " no-combining resets").Tol("0").Sev("H"));
            T.Check(pre + "HARQ buffer length when the next copy is decoded = 2 (leader + this copy)", A.secondHist1 == A.second,
                    Opt().Exp("hist_before = 1 for all").Obs(P(A.secondHist1, A.second)).Tol("0").Sev("H"));
            if (!weak)
            {
                T.Check(pre + "J decodes the TB on the combined copy", A.secondOk >= 0.99 * A.second,
                        Opt().Exp(">= 0.99 (model TBLER ~0)").Obs(P(A.secondOk, A.second) + ", effective SINR " + Fmt(A.effSecond / A.second, 2) + " dB")
                            .Tol("1 %").Sev("H"));
            }
            else
            {
                T.CheckXfail(pre + "the strong helper copy (10 dB, decodes alone) still decodes when combined with the weak 0 dB leader copy",
                             A.secondOk >= 0.99 * A.second,
                             Opt().Fnd("F35").Sev("M").Exp(">= 0.99 (as alone, see R6_weak_sc0)")
                                 .Obs(P(A.secondOk, A.second) + ", effective SINR " + Fmt(A.effSecond / A.second, 2) + " dB; the helper's second copy (3 copies combined) " +
                                      P(A.thirdOk, A.third) + "; PRR of J " + Fmt(A.prrJ, 3))
                                 .Note("F35: the combined SINR is the EESM mean of the 0 dB and the 10 dB copy; the TB still gets through on the "
                                       "second copy, i.e. combining costs one extra copy here instead of saving one"));
            }
        }
        if (noCombine)
        {
            T.Check(pre + "every decode at J starts from an empty buffer (combining off)", A.decodesJHist0 == A.decodesJ && A.noCombineReset > 0,
                    Opt().Exp("hist_before = 0 for all decodes, buffer cleared before each").Obs(P(A.decodesJHist0, A.decodesJ) + ", " + FmtI(A.noCombineReset) + " RESET_NO_COMBINE")
                        .Tol("0").Sev("H"));
            if (!weak)
            {
                T.Check(pre + "J does not decode the TB on the next copy alone", A.secondOk <= 0.01 * A.second,
                        Opt().Exp("<= 0.01 (model TBLER alone ~1)").Obs(P(A.secondOk, A.second) + "; PRR of J " + Fmt(A.prrJ, 3)).Tol("1 %").Sev("H"));
            }
            else
            {
                T.Check(pre + "the strong helper copy decodes alone", A.secondOk >= 0.99 * A.second,
                        Opt().Exp(">= 0.99").Obs(P(A.secondOk, A.second) + "; PRR of J " + Fmt(A.prrJ, 3)).Tol("1 %").Sev("H"));
            }
        }
        T.Metric(name + "_second_ok", A.second ? static_cast<double>(A.secondOk) / A.second : NAN);
        T.Metric(name + "_prr_J", A.prrJ);
    }
    return T.Finish();
}
