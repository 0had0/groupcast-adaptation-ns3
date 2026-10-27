// C07_stale_harq_state (W: SYN_STALE): after a member misses the SCI of copy 1 of a new TB, is the HARQ process of
// that TB flushed before its later copies are decoded (3GPP NDI toggle, TS 38.321 5.22.2.2 / TS 38.212 8.4.1.1)?
//
// The simulator's NDI is a "new data" flag (1 on copy 1 only). A receiver that misses copy 1 then sees copies 2 / 3
// with NDI 0 and, before the fix: (A) skips them as "already decoded" when it decoded the previous TB on that HARQ id
// (and ACKs them under Option 2, SlAckPrevDecodedTb); (B) soft-combines them with the failed copies of the previous TB
// when it did not. 3GPP: the NDI bit toggles per TB, the receiver compares it with the bit stored for the process and
// flushes the process on a toggle.
//
// SYN_STALE (campaign/runsets.py STALE_CASES): the SYN_COMB geometry (L = 0, H = 1, J = 2), PSSCH SINR fixed by
// --testFixedSinr, J ignores selected PSSCH receptions (--testDropSci, both SCI stages unread, event SCI_DROPPED in
// _harq.csv). Per drop of a copy 1 (an "episode"): the previous TB on the HARQ id (decoded or still in the buffer),
// J's later events of the new TB: first attempt = a decode from an empty buffer, the next one combined with it only,
// no ACK because of the previous TB, the TB decoded. Regression cases (e_*): nothing dropped, combining as before.
#include "common/csv.h"
#include "common/harness.h"
#include "common/runsets.h"

#include <cmath>
#include <map>
#include <set>
#include <string>
#include <vector>

using namespace ptest;

namespace
{

constexpr int kJ = 2; //!< receiver J (member / node 2)

enum Ev
{
    DECODE = 0,
    REMOVE_PREV_NDI = 1,
    SKIP_PREV_DECODED = 2,
    RESET_NO_COMBINE = 3,
    RESET_NDI = 4,
    RESET_DECODED = 5,
    SCI2_FAIL = 6,
    SCI_DROPPED = 7,
    ACK_PREV_DECODED = 8,
    NDI_TOGGLE = 9,
};

/// one _harq.csv row of J, with the leader TB it carries and the copy index (from _att.csv)
struct HEv
{
    double tMs;
    int txNode, harqId, event, hist, corrupt, harqFb;
    int64_t tb{-1};
    int txInTb{0}, attNdi{-1};
};

/// per-case aggregate over the seeds
struct CaseAgg
{
    long runs{0};
    // episodes = drops of a leader copy 1
    long episodes{0}, prevDecoded{0}, prevBuffered{0}, sameHarqId{0};
    long laterCopy{0};         // a later copy of the TB reaches J
    long firstSkipped{0};      // ... and is skipped as "already decoded" before J decodes the TB
    long firstDecode{0};       // the first decode attempt of the TB
    long firstHist0{0};        // ... from an empty buffer
    long falseAck{0};          // ACK_PREV_DECODED of the TB before J decoded it
    long secondDecode{0}, secondHist1{0}; // the next decode after a failed first one, combined with it only
    long tbDecoded{0};
    std::string ex;
    // every decode of J: hist_before == earlier failed decodes of the same TB (buffer = this TB's copies only)
    long decodes{0}, histForeign{0}, histNonZero{0};
    std::string histEx;
    long skips{0}, ackPrev{0}, skipFb{0}, noCombineResets{0}, tbs{0}, tbsDecoded{0}, maxDecodesPerTb{0};
    // run-level counter of the fix
    long toggleRuns{0}, toggleSum{0};
};

bool
LoadJ(const RunRef& r, std::vector<HEv>& ev)
{
    Table h, a;
    if (!LoadTable(r.prefix + "_harq.csv", h) || !LoadTable(r.prefix + "_att.csv", a))
    {
        return false;
    }
    struct AttInfo
    {
        int64_t tb;
        int txInTb, ndi;
    };
    std::map<std::pair<int, int64_t>, AttInfo> attOf; // (side, slot) -> TB, copy, NDI flag (J's rows)
    for (size_t i = 0; i < a.nrows; ++i)
    {
        if (static_cast<int>(a.At("member", i)) != kJ)
        {
            continue;
        }
        attOf[{static_cast<int>(a.At("side", i)), static_cast<int64_t>(a.At("slot_abs", i))}] = {
            static_cast<int64_t>(a.At("tb", i)), static_cast<int>(a.At("tx_in_tb", i)),
            static_cast<int>(a.At("ndi", i))};
    }
    for (size_t i = 0; i < h.nrows; ++i)
    {
        if (static_cast<int>(h.At("member", i)) != kJ)
        {
            continue;
        }
        HEv e;
        e.tMs = h.At("t_ms", i);
        e.txNode = static_cast<int>(h.At("tx_node", i));
        e.harqId = static_cast<int>(h.At("harq_id", i));
        e.event = static_cast<int>(h.At("event", i));
        e.hist = static_cast<int>(h.At("hist_before", i));
        e.corrupt = static_cast<int>(h.At("corrupt", i));
        e.harqFb = static_cast<int>(h.At("harq_fb", i));
        const int side = (e.txNode == 0) ? 0 : 1;
        auto it = attOf.find({side, static_cast<int64_t>(std::floor(e.tMs / 0.5))});
        if (it != attOf.end())
        {
            e.tb = it->second.tb;
            e.txInTb = it->second.txInTb;
            e.attNdi = it->second.ndi;
        }
        ev.push_back(e);
    }
    return true;
}

void
Analyse(const RunRef& r, bool combining, CaseAgg& A)
{
    std::vector<HEv> ev;
    if (!LoadJ(r, ev))
    {
        return;
    }
    A.runs++;
    // per TB: decoded by J? (and when)
    std::map<int64_t, double> decodedAt;
    std::map<int64_t, long> decodesOf;
    std::set<int64_t> tbsSeen;
    for (const HEv& e : ev)
    {
        if (e.tb < 0)
        {
            continue;
        }
        tbsSeen.insert(e.tb);
        if (e.event == DECODE && e.corrupt == 0 && !decodedAt.count(e.tb))
        {
            decodedAt[e.tb] = e.tMs;
        }
    }
    A.tbs += static_cast<long>(tbsSeen.size());
    A.tbsDecoded += static_cast<long>(decodedAt.size());
    // invariants on every decode: the buffer holds only this TB's earlier (failed) copies
    std::map<int64_t, long> failedBefore;
    for (const HEv& e : ev)
    {
        if (e.event == SKIP_PREV_DECODED)
        {
            A.skips++;
            A.skipFb += e.harqFb;
        }
        A.ackPrev += (e.event == ACK_PREV_DECODED);
        A.noCombineResets += (e.event == RESET_NO_COMBINE);
        if (e.event != DECODE || e.tb < 0)
        {
            continue;
        }
        A.decodes++;
        const long expHist = combining ? failedBefore[e.tb] : 0;
        if (e.hist != expHist)
        {
            A.histForeign++;
            if (A.histEx.size() < 240)
            {
                A.histEx += "s" + std::to_string(r.seed) + " t=" + Fmt(e.tMs, 3) + " TB " + std::to_string(e.tb) +
                            " hist " + std::to_string(e.hist) + "!=" + std::to_string(expHist) + "; ";
            }
        }
        A.histNonZero += (e.hist != 0);
        if (e.corrupt)
        {
            failedBefore[e.tb]++;
        }
        A.maxDecodesPerTb = std::max(A.maxDecodesPerTb, ++decodesOf[e.tb]);
    }
    // episodes: J ignored a leader copy 1
    for (size_t i = 0; i < ev.size(); ++i)
    {
        const HEv& d = ev[i];
        if (d.event != SCI_DROPPED)
        {
            continue;
        }
        if (d.txNode != 0 || d.txInTb != 1 || d.attNdi != 1)
        {
            continue; // a later copy or a helper copy, dropped to leave the previous TB in the buffer
        }
        A.episodes++;
        const int64_t tb = d.tb;
        // the previous leader TB at J (its HARQ id from J's events) and its state
        int64_t prev = -1;
        int prevHarq = -1;
        for (const HEv& e : ev)
        {
            if (e.txNode == 0 && e.tb >= 0 && e.tb < tb && e.tb > prev && e.event == DECODE)
            {
                prev = e.tb;
            }
        }
        bool prevDec = false;
        bool prevBuf = false;
        for (const HEv& e : ev)
        {
            if (e.tb != prev || e.tMs >= d.tMs)
            {
                continue;
            }
            if (e.event == DECODE)
            {
                prevHarq = e.harqId;
                prevDec = prevDec || e.corrupt == 0;
                prevBuf = true;
            }
        }
        prevBuf = prevBuf && !prevDec;
        A.prevDecoded += prevDec;
        A.prevBuffered += prevBuf;
        // J's later events of this TB
        bool decoded = false;
        bool skippedFirst = false;
        bool firstSeen = false;
        int nDec = 0;
        int firstCorrupt = 0;
        std::string trail;
        for (size_t k = i + 1; k < ev.size(); ++k)
        {
            const HEv& e = ev[k];
            if (e.tb != tb || e.event == SCI_DROPPED)
            {
                continue;
            }
            if (!firstSeen && (e.event == DECODE || e.event == SKIP_PREV_DECODED))
            {
                firstSeen = true;
                A.laterCopy++;
                A.sameHarqId += (e.harqId == prevHarq);
                if (e.event == SKIP_PREV_DECODED)
                {
                    skippedFirst = true;
                }
            }
            static const char* names[] = {"DEC",    "RMPREV", "SKIP", "RSTNC",  "RSTNDI",
                                          "RSTDEC", "SCI2F",  "DROP", "ACKPREV", "TOGGLE"};
            if (trail.size() < 120)
            {
                trail += std::string(e.event >= 0 && e.event <= NDI_TOGGLE ? names[e.event] : "?") + "(tx" +
                         std::to_string(e.txNode) + ",h" + std::to_string(e.hist) +
                         (e.event == DECODE ? (e.corrupt ? ",x" : ",ok") : "") + ") ";
            }
            if (e.event == ACK_PREV_DECODED && !decoded)
            {
                A.falseAck++;
            }
            if (e.event == DECODE)
            {
                ++nDec;
                if (nDec == 1)
                {
                    A.firstDecode++;
                    A.firstHist0 += (e.hist == 0);
                    firstCorrupt = e.corrupt;
                }
                else if (nDec == 2 && firstCorrupt)
                {
                    A.secondDecode++;
                    A.secondHist1 += (e.hist == 1);
                }
                decoded = decoded || e.corrupt == 0;
            }
        }
        A.firstSkipped += skippedFirst;
        A.tbDecoded += decoded;
        if (A.ex.size() < 400)
        {
            A.ex += "s" + std::to_string(r.seed) + " TB " + std::to_string(tb) + " (prev TB " + std::to_string(prev) +
                    (prevDec ? " decoded" : (prevBuf ? " in buffer" : " ?")) + "): " + trail + "; ";
        }
    }
    // run-level counter of the fix
    Table run;
    if (LoadTable(r.prefix + "_run.csv", run))
    {
        if (run.Has("ndi_toggle_new_tb"))
        {
            A.toggleRuns++;
            A.toggleSum += static_cast<long>(run.R0("ndi_toggle_new_tb"));
        }
    }
}

std::string
P(long k, long n)
{
    return FmtI(k) + "/" + FmtI(n);
}

} // namespace

int
main()
{
    TestReport T("C07", "stale_harq_state", "C");
    T.Summary("After a member misses the SCI of copy 1 of a new TB, the later copies of that TB are decoded from a flushed "
              "HARQ process: not skipped as the previous TB on the HARQ id, not ACKed because of it, not combined with "
              "its copies (3GPP NDI toggle).");
    T.AddRef("TS 38.321 5.22.2.2 (SL HARQ: NDI toggled = new transmission, the process is flushed)");
    T.AddRef("TS 38.212 8.4.1.1 (SCI format 2-A: NDI, 1 bit)");
    T.AddRef("nr-spectrum-phy.cc RxSlPssch (IsPrevDecoded / RemovePrevDecoded / ResetSlDataHarqProcessStatus)");
    T.AddRunset("SYN_STALE");

    const auto all = LoadManifest("SYN_STALE");
    if (all.empty())
    {
        T.Skip("SYN_STALE runs", "no SYN_STALE manifest: python3 campaign/run_matrix.py --runset SYN_STALE");
        return T.Finish();
    }
    std::map<std::string, CaseAgg> agg;
    for (const auto& r : all)
    {
        const bool combining = r.arm != "e_R1"; // R1 without SlBlindSoftCombining decodes every copy alone
        Analyse(r, combining, agg[r.arm]);
        T.AddNRuns(1);
    }
    CsvWriter cw(ResultsDir() + "/C07_episodes.csv",
                 "case,runs,episodes,prev_decoded,prev_buffered,later_copy,first_skipped,first_decode,first_hist0,"
                 "false_ack,second_decode,second_hist1,tb_decoded,decodes,hist_foreign,skips,ack_prev,tbs,tbs_decoded,"
                 "ndi_toggle_new_tb");
    for (const auto& [name, A] : agg)
    {
        cw.Row({name, FmtI(A.runs), FmtI(A.episodes), FmtI(A.prevDecoded), FmtI(A.prevBuffered), FmtI(A.laterCopy),
                FmtI(A.firstSkipped), FmtI(A.firstDecode), FmtI(A.firstHist0), FmtI(A.falseAck), FmtI(A.secondDecode),
                FmtI(A.secondHist1), FmtI(A.tbDecoded), FmtI(A.decodes), FmtI(A.histForeign), FmtI(A.skips),
                FmtI(A.ackPrev), FmtI(A.tbs), FmtI(A.tbsDecoded), A.toggleRuns ? FmtI(A.toggleSum) : "NA"});
    }
    T.Artefact("results/C07_episodes.csv");

    for (auto& [name, A] : agg)
    {
        const std::string pre = "SYN_STALE " + name + ": ";
        if (A.runs == 0)
        {
            T.Skip(pre + "runs", "no complete run");
            continue;
        }
        const char c = name[0];
        const bool option2 = name.find("R3") != std::string::npos || name.find("R6") != std::string::npos;
        if (c == 'e')
        {
            // ---------------- regression, nothing dropped
            T.Check(pre + "every decode combines only earlier failed copies of the same TB" +
                        (name == "e_R1" ? " (blind, SlBlindSoftCombining off: none)" : ""),
                    A.decodes > 0 && A.histForeign == 0,
                    Opt().Exp("hist_before = number of earlier failed decodes of the TB" +
                              std::string(name == "e_R1" ? ", here 0" : ""))
                        .Obs(FmtI(A.histForeign) + " of " + FmtI(A.decodes) + " decodes differ" + (A.histEx.empty() ? "" : ": " + A.histEx))
                        .Tol("0").Sev("H"));
            if (name == "e_R3" || name == "e_R6" || name == "e_R1_bsc")
            {
                T.Check(pre + "J decodes every TB on the combined second copy", A.tbs > 0 && A.tbsDecoded == A.tbs && A.maxDecodesPerTb == 2 &&
                                                                                    A.histNonZero > 0,
                        Opt().Exp("all TBs decoded, 2 decodes per TB (alone TBLER 1, two combined 0)")
                            .Obs(P(A.tbsDecoded, A.tbs) + " TBs decoded, at most " + FmtI(A.maxDecodesPerTb) + " decodes per TB, " +
                                 FmtI(A.histNonZero) + " combined decodes")
                            .Tol("0").Sev("H"));
            }
            if (name == "e_R3_3copies")
            {
                T.Check(pre + "copies 2 and 3 are combined with copy 1 (buffer of 1 then 2 copies)", A.maxDecodesPerTb == 3 && A.histForeign == 0,
                        Opt().Exp("3 decodes per TB with hist_before 0, 1, 2").Obs("at most " + FmtI(A.maxDecodesPerTb) + " decodes per TB, " +
                                                                              FmtI(A.histForeign) + " foreign")
                            .Tol("0").Sev("H"));
            }
            if (name == "e_R3_decoded")
            {
                T.Check(pre + "a copy of a TB J already decoded is skipped and ACKed (SlAckPrevDecodedTb)",
                        A.skips > 0 && A.ackPrev == A.skipFb && A.skipFb == A.skips,
                        Opt().Exp("skips > 0, one ACK per skipped copy with HARQ feedback enabled")
                            .Obs(FmtI(A.skips) + " skipped (" + FmtI(A.skipFb) + " with HARQ feedback), " + FmtI(A.ackPrev) + " ACKs")
                            .Tol("0").Sev("H"));
            }
            if (name == "e_R1")
            {
                T.Check(pre + "blind copies without SlBlindSoftCombining are decoded alone (buffer cleared before each)",
                        A.decodes > 0 && A.histNonZero == 0 && A.noCombineResets >= A.decodes,
                        Opt().Exp("hist_before = 0 for every decode, one RESET_NO_COMBINE per decode")
                            .Obs(FmtI(A.histNonZero) + " combined decodes, " + FmtI(A.noCombineResets) + " resets for " + FmtI(A.decodes) + " decodes")
                            .Tol("0").Sev("H"));
            }
            T.Check(pre + "no copy-1 drop, so the NDI toggle never finds a new TB the \"new data\" flag missed",
                    A.toggleRuns == A.runs && A.toggleSum == 0,
                    Opt().Exp("ndi_toggle_new_tb = 0 in every run")
                        .Obs(A.toggleRuns ? FmtI(A.toggleSum) + " in " + FmtI(A.toggleRuns) + " runs" : "ndi_toggle_new_tb not in _run.csv")
                        .Tol("0").Sev("M"));
            continue;
        }

        // ---------------- episodes: a leader copy 1 ignored at J
        const bool stale = (c == 'c') || name.find("stale") != std::string::npos;
        T.Check(pre + "setup: J ignores a leader copy 1 and a later copy of that TB reaches J; the previous TB on the HARQ id was " +
                    (stale ? "not decoded (its copy 1 in the buffer)" : "decoded"),
                A.episodes == A.runs && A.laterCopy == A.episodes && A.sameHarqId == A.episodes &&
                    (stale ? A.prevBuffered == A.episodes : A.prevDecoded == A.episodes),
                Opt().Exp("one episode per run, a later copy on the same HARQ id, previous TB " + std::string(stale ? "in the buffer" : "decoded"))
                    .Obs(FmtI(A.episodes) + " episodes in " + FmtI(A.runs) + " runs; later copy " + P(A.laterCopy, A.episodes) +
                         ", same HARQ id " + P(A.sameHarqId, A.episodes) + "; previous TB decoded " + P(A.prevDecoded, A.episodes) +
                         ", in the buffer " + P(A.prevBuffered, A.episodes))
                    .Tol("0").Sev("H").Note("the run-set's k values are J's receptions at seed 1 (campaign/runsets.py STALE_CASES)"));
        if (!stale)
        {
            T.Check(pre + "the later copy is not skipped as the previous TB (\"already decoded\")", A.laterCopy > 0 && A.firstSkipped == 0,
                    Opt().Exp("0").Obs(P(A.firstSkipped, A.laterCopy) + " skipped; " + A.ex).Tol("0").Sev("H"));
        }
        T.Check(pre + "J's first decode of the TB starts from an empty buffer (process flushed)", A.firstDecode == A.episodes && A.firstHist0 == A.firstDecode,
                Opt().Exp("a decode attempt with hist_before = 0 in every episode")
                    .Obs(FmtI(A.firstDecode) + " decode attempts in " + FmtI(A.episodes) + " episodes, " + FmtI(A.firstHist0) + " from an empty buffer; " + A.ex)
                    .Tol("0").Sev("H"));
        if (option2)
        {
            T.Check(pre + "no ACK of the TB because of the previous TB (Option 2, SlAckPrevDecodedTb)", A.falseAck == 0,
                    Opt().Exp("0 ACK_PREV_DECODED before J decodes the TB").Obs(FmtI(A.falseAck)).Tol("0").Sev("H")
                        .Note("a false ACK stops the leader (R3) or the helper (R4.2, R6: relayOverheardAcks)"));
        }
        T.Check(pre + "the next copy after a failed first decode is combined with it only (buffer of 1)", A.secondDecode > 0 && A.secondHist1 == A.secondDecode,
                Opt().Exp("hist_before = 1").Obs(P(A.secondHist1, A.secondDecode)).Tol("0").Sev("H"));
        T.Check(pre + "J decodes the TB (two later copies combined)", A.tbDecoded == A.episodes,
                Opt().Exp(FmtI(A.episodes)).Obs(P(A.tbDecoded, A.episodes)).Tol("0").Sev("H"));
        T.Check(pre + "every decode combines only earlier failed copies of the same TB", A.decodes > 0 && A.histForeign == 0,
                Opt().Exp("hist_before = number of earlier failed decodes of the TB")
                    .Obs(FmtI(A.histForeign) + " of " + FmtI(A.decodes) + " decodes differ" + (A.histEx.empty() ? "" : ": " + A.histEx))
                    .Tol("0").Sev("H"));
        T.Check(pre + "the NDI toggle finds the new TB the \"new data\" flag missed", A.toggleRuns == A.runs && A.toggleSum >= A.episodes,
                Opt().Exp("ndi_toggle_new_tb >= 1 per episode")
                    .Obs(A.toggleRuns ? FmtI(A.toggleSum) + " in " + FmtI(A.toggleRuns) + " runs" : "ndi_toggle_new_tb not in _run.csv")
                    .Tol("0").Sev("M"));
        T.Metric(name + "_tb_decoded", A.episodes ? static_cast<double>(A.tbDecoded) / A.episodes : NAN);
    }
    return T.Finish();
}
