// A02_psfch_timing (U + M): PSFCH occasion arithmetic (TS 38.213 16.3: first PSFCH slot at least sl-MinTimeGapPSFCH slots
// after the PSSCH) on the study (dedicated) pool and on the shared TDD pool; in-process micro scenario that checks PSFCH
// slot, content and HARQ id end to end (Option 2 cast type).
#include "common/csv.h"
#include "common/harness.h"
#include "common/micro_sl.h"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <fstream>
#include <map>
#include <random>
#include <sstream>

using namespace ptest;

namespace
{

/// the controller's arithmetic (nack_only_harq.cc:343-357): k = gap; while(!hasPsfch(n+k) && k<64) ++k; occasion = n+k
int64_t
ControllerOccasion(const std::vector<uint8_t>& mask, int64_t n, int gap, int* kOut = nullptr)
{
    int k = gap;
    while (!(n + k < static_cast<int64_t>(mask.size()) && mask[n + k]) && k < 64)
    {
        ++k;
    }
    if (kOut)
    {
        *kOut = k;
    }
    return n + k;
}

/// independent: smallest PSFCH slot s with s >= n + gap
int64_t
IndependentOccasion(const std::vector<uint8_t>& mask, int64_t n, int gap)
{
    for (int64_t s = n + gap; s < static_cast<int64_t>(mask.size()); ++s)
    {
        if (mask[s])
        {
            return s;
        }
    }
    return -1;
}

std::string
ReadFile(const std::string& p)
{
    std::ifstream f(p);
    std::ostringstream os;
    os << f.rdbuf();
    return os.str();
}

} // namespace

int
main()
{
    TestReport T("A02", "psfch_timing", "A");
    T.Summary("PSFCH occasions recur every sl-PSFCH-Period SL slots; the controller's m(n) arithmetic equals an independent "
              "derivation; end to end (micro scenario) every PSSCH reception is answered in slot m(n) with the right content "
              "and HARQ id.");
    T.AddRef("TS 38.213 16.3 (PSFCH slot: first slot with PSFCH resources at least sl-MinTimeGapPSFCH slots of the pool after "
             "the PSSCH) [V]");
    T.AddRef("TS 38.331 sl-PSFCH-Period, sl-MinTimeGapPSFCH");

    std::mt19937 rng(7);

    // ---- (U) pool arithmetic ---------------------------------------------------------------------------------------
    std::map<std::pair<bool, int>, MicroLog> poolLogs; // (dedicated, period) -> log
    for (bool ded : {true, false})
    {
        for (int per : {1, 2, 4})
        {
            MicroCfg c;
            c.dedicated = ded;
            c.psfchPeriod = static_cast<uint16_t>(per);
            c.nPackets = 2;
            c.firstPacketS = 2.5;
            c.extraSimS = 0.1;
            MicroLog lg;
            std::string err;
            if (!RunMicro(c, lg, &err))
            {
                T.Fail(std::string("setup/") + (ded ? "dedicated" : "shared") + " period " + std::to_string(per), Opt().Obs(err));
                continue;
            }
            poolLogs[{ded, per}] = lg;
        }
    }
    for (bool ded : {true, false})
    {
        const std::string pname = ded ? "study pool (dedicated carrier)" : "shared TDD pool";
        auto it1 = poolLogs.find({ded, 1});
        if (it1 == poolLogs.end())
        {
            continue;
        }
        const std::vector<uint8_t>& sl = it1->second.psfchSlotMask; // period 1: PSFCH in every SL slot = the SL slot set
        long nSl = 0;
        for (uint8_t b : sl) nSl += b;
        for (int per : {2, 4})
        {
            auto it = poolLogs.find({ded, per});
            if (it == poolLogs.end())
            {
                continue;
            }
            const auto& m = it->second.psfchSlotMask;
            // every PSFCH slot is an SL slot; consecutive PSFCH slots are `per` SL slots apart
            long idx = 0, prevIdx = -1;
            bool ok = true;
            long subset = 0;
            for (size_t s = 0; s < m.size(); ++s)
            {
                if (sl[s])
                {
                    if (m[s])
                    {
                        if (prevIdx >= 0 && idx - prevIdx != per)
                        {
                            ok = false;
                        }
                        prevIdx = idx;
                    }
                    ++idx;
                }
                else if (m[s])
                {
                    subset++;
                }
            }
            T.Check("pool/" + pname + ": PSFCH slots recur every " + std::to_string(per) + " SL slots", ok && subset == 0,
                    Opt().Exp("consecutive PSFCH slots exactly " + std::to_string(per) + " SL slots apart, all inside the SL set")
                        .Obs(std::string(ok ? "ok" : "gap mismatch") + ", PSFCH slots outside SL set: " + FmtI(subset))
                        .Ref("TS 38.213 16.3 [V]; NrSlCommResourcePool::SlotHasPsfch"));
        }
        // m(n): controller vs independent
        for (int per : {1, 2, 4})
        {
            auto it = poolLogs.find({ded, per});
            if (it == poolLogs.end())
            {
                continue;
            }
            const auto& m = it->second.psfchSlotMask;
            long agree = 0, n = 10000, physVsLogical = 0;
            std::uniform_int_distribution<int64_t> un(0, static_cast<int64_t>(m.size()) - 200);
            // logical variant (3GPP): at least K SL slots of the pool after n, then the first PSFCH slot
            for (long i = 0; i < n; ++i)
            {
                const int64_t slot = un(rng);
                const int64_t a = ControllerOccasion(m, slot, 3);
                const int64_t b = IndependentOccasion(m, slot, 3);
                agree += (a == b) ? 1 : 0;
                // logical: slot index in SL slots
                const auto& slm = it1->second.psfchSlotMask;
                int64_t cnt = 0, s = slot;
                while (cnt < 3 && s + 1 < static_cast<int64_t>(m.size()))
                {
                    ++s;
                    cnt += slm[s] ? 1 : 0;
                }
                int64_t lg = s;
                while (lg < static_cast<int64_t>(m.size()) && !m[lg])
                {
                    ++lg;
                }
                physVsLogical += (lg != a) ? 1 : 0;
            }
            T.Check("occasion/" + pname + " period " + std::to_string(per) + ": controller arithmetic == independent (10,000 random n)",
                    agree == n, Opt().Exp("10000 equal").Obs(FmtI(agree) + " equal").Tol("0").Sev("M"));
            if (!ded)
            {
                T.Info("occasion/" + pname + " period " + std::to_string(per) + ": physical-slot gap vs logical (3GPP) gap",
                       Opt().Obs(Fmt(100.0 * static_cast<double>(physVsLogical) / n, 4) + " % of n differ")
                           .Exp("3GPP counts the K slots in resource-pool (logical) slots [V]")
                           .Note("on the shared TDD pool NrUePhy::SlFeedback compares physical slot numbers (nr-ue-phy.cc:1959-1975); "
                                 "not relevant for the study (dedicated carrier: logical == physical)"));
            }
        }
    }

    // ---- F21: the literal 3 ------------------------------------------------------------------------------------------
    {
        const std::string main = ReadFile(EvalDir() + "/platoon_harq_robustness_eval.cc");
        const std::string stack = ReadFile(RepoRoot() + "/scratch_overlay/common/stack/nr_sl_stack.cc");
        const bool inStack = stack.find("SetSlMinTimeGapPsfch(3)") != std::string::npos;
        const bool inMain1 = main.find("ctl.Install(leaderDev, memberDevs, 254, hMode, psfchPeriod ? 3 : 0)") != std::string::npos;
        const bool inMain2 = main.find("hctl.Install(helperDev, others, 254, false, 3)") != std::string::npos;
        T.Check("F21/controller literal 3 == pool SetSlMinTimeGapPsfch(3)", inStack && inMain1 && inMain2,
                Opt().Exp("3 in nr_sl_stack.cc:394 and in both controller installs (main.cc:1995, 2052)")
                    .Obs(std::string("stack literal ") + (inStack ? "3" : "?") + ", leader controller " +
                         (inMain1 ? "3" : "?") + ", helper controller " + (inMain2 ? "3" : "?"))
                    .Fnd("F21").Sev("L")
                    .Note("two literals that must stay in sync; the micro scenario below confirms the operational minimum gap is 3 slots"));
        T.Check("F21/sl-MinTimeGapPSFCH 3 in {2,3}", true, Opt().Exp("{2,3}").Obs("3").Ref("TS 38.331 [V]"));
    }

    // ---- (M) micro scenario -----------------------------------------------------------------------------------------
    std::map<int, std::map<int, long>> gapHist; // period -> (psfch slot - pssch slot) -> count
    for (int per : {1, 2, 4})
    {
        MicroCfg c;
        c.psfchPeriod = static_cast<uint16_t>(per);
        c.rxDistM = {30.0, 300.0, 600.0, 900.0};
        c.nPackets = 100;
        c.intervalMs = 40.0;
        MicroLog lg;
        std::string err;
        if (!RunMicro(c, lg, &err))
        {
            T.Fail("micro/period " + std::to_string(per), Opt().Obs(err));
            continue;
        }
        const int nRx = static_cast<int>(c.rxDistM.size());
        if (std::getenv("PTEST_DEBUG") && per == 1)
        {
            for (size_t k = 0; k < 3 && k < lg.rx.size(); ++k)
                std::cout << "DBG rx t=" << lg.rx[k].t << " slot=" << lg.rx[k].slot << " t/0.5ms=" << lg.rx[k].t / 0.0005 << "\n";
            for (size_t k = 0; k < 3 && k < lg.fbTx.size(); ++k)
                std::cout << "DBG fbTx t=" << lg.fbTx[k].t << " slot=" << lg.fbTx[k].slot << " rx=" << lg.fbTx[k].rx << "\n";
            for (size_t k = 0; k < 3 && k < lg.tx.size(); ++k)
                std::cout << "DBG tx t=" << lg.tx[k].t << " slot=" << lg.tx[k].slot << "\n";
            std::cout << "DBG mask psfch first: ";
            for (int q = 0; q < 12; ++q) std::cout << int(lg.psfchSlotMask[q]);
            std::cout << "\n";
        }
        long nRxEv = 0, nFb = 0, nContent = 0, nSlot = 0, nTxRecv = 0, nSci2NoFb = 0, nSci2 = 0, nAck = 0, nNack = 0;
        long minGap = 1000;
        std::vector<bool> usedFb(lg.fbQueued.size(), false);
        std::vector<bool> usedRx(lg.fbRx.size(), false);
        const double slotS = 1e-3 / lg.slotsPerSubframe;
        for (const auto& e : lg.rx)
        {
            ++nRxEv;
            // feedback queued by this RX at (about) the decode time
            long best = -1;
            for (size_t k = 0; k < lg.fbQueued.size(); ++k)
            {
                if (usedFb[k] || lg.fbQueued[k].rx != e.rx)
                {
                    continue;
                }
                if (lg.fbQueued[k].t >= e.t - 1e-9 && lg.fbQueued[k].t < e.t + 2 * slotS)
                {
                    best = static_cast<long>(k);
                    break;
                }
            }
            if (e.sci2Corrupt)
            {
                ++nSci2;
                nSci2NoFb += (best < 0) ? 1 : 0;
                continue;
            }
            if (best < 0)
            {
                continue; // e.g. retransmission of a TB the RX has already decoded: no new feedback in stock NR
            }
            usedFb[best] = true;
            const auto& f = lg.fbQueued[best];
            ++nFb;
            (f.ack ? nAck : nNack)++;
            nContent += (f.ack == !(e.corrupt || e.sci2Corrupt)) ? 1 : 0;
            // expected PSFCH slot
            const int64_t mN = ControllerOccasion(lg.psfchSlotMask, e.slot, 3);
            bool slotOk = false;
            for (const auto& ft : lg.fbTx)
            {
                if (ft.rx == e.rx && ft.slot == mN)
                {
                    slotOk = true;
                    gapHist[per][static_cast<int>(ft.slot - e.slot)]++;
                    minGap = std::min<long>(minGap, ft.slot - e.slot);
                    break;
                }
            }
            nSlot += slotOk ? 1 : 0;
            // the TX receives the feedback with the same HARQ id from this RX
            bool recv = false;
            for (size_t k = 0; k < lg.fbRx.size(); ++k)
            {
                if (usedRx[k])
                {
                    continue;
                }
                const auto& r = lg.fbRx[k];
                if (r.harq == f.harq && r.ack == f.ack && r.rxRnti == f.rxRnti && r.t >= f.t &&
                    r.t < f.t + 12 * slotS)
                {
                    usedRx[k] = true;
                    recv = true;
                    break;
                }
            }
            nTxRecv += recv ? 1 : 0;
        }
        (void)nRx;
        const std::string tag = "micro/psfchPeriod=" + std::to_string(per);
        T.Check(tag + ": have feedback events", nFb >= 50, Opt().Exp(">= 50").Obs(FmtI(nFb) + " feedback events from " + FmtI(nRxEv) + " PSSCH receptions (ACK " + FmtI(nAck) + ", NACK " + FmtI(nNack) + ")"));
        T.Check(tag + ": PSFCH content == decode outcome (ACK iff TB decoded)", nFb > 0 && nContent == nFb,
                Opt().Exp("100% (" + FmtI(nFb) + ")").Obs(FmtI(nContent) + "/" + FmtI(nFb)).Tol("0").Sev("H").Ref("TS 38.213 16.3, TS 38.321 5.22.1.3 [V]"));
        T.Check(tag + ": PSFCH transmitted exactly in slot m(n) = first PSFCH slot >= n + 3", nFb > 0 && nSlot == nFb,
                Opt().Exp("100% (" + FmtI(nFb) + ")").Obs(FmtI(nSlot) + "/" + FmtI(nFb) + "; minimum PSSCH-to-PSFCH gap " + FmtI(minGap) + " slots")
                    .Tol("0").Sev("H").Ref("TS 38.213 16.3 [V]"));
        T.Check(tag + ": TX receives each feedback with the same HARQ id and content", nFb > 0 && nTxRecv == nFb,
                Opt().Exp("100% (" + FmtI(nFb) + ")").Obs(FmtI(nTxRecv) + "/" + FmtI(nFb)).Tol("0").Sev("H"));
        T.Check(tag + ": no feedback when SCI-2 was not decoded (DTX)", nSci2NoFb == nSci2,
                Opt().Exp("0 feedbacks for " + FmtI(nSci2) + " SCI-2 failures").Obs(FmtI(nSci2 - nSci2NoFb) + " unexpected feedbacks").Ref("TS 38.213 16.3 [V]"));
        if (per == 1)
        {
            T.Check("F21/operational minimum PSSCH-to-PSFCH gap equals sl-MinTimeGapPSFCH = 3 slots (period 1)", minGap == 3,
                    Opt().Exp("3").Obs(FmtI(minGap)).Fnd("F21"));
        }
    }
    // histogram artefact
    {
        const std::string rel = "results/A02_psfch_gap_hist.csv";
        CsvWriter w(TestsRoot() + "/" + rel, "psfch_period,gap_slots,count");
        for (const auto& [per, h] : gapHist)
        {
            for (const auto& [gap, cnt] : h)
            {
                w.Row({std::to_string(per), std::to_string(gap), std::to_string(cnt)});
            }
        }
        T.Artefact(rel);
    }
    return T.Finish();
}
