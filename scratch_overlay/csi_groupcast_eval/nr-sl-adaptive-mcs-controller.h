// SPDX-License-Identifier: GPL-2.0-only
#pragma once

// NrSlAdaptiveMcsController
// ==========================
// Two adaptation families share one controller:
//
//   Mode::NackRate        Legacy: sliding-window NACK rate → step up/down on
//                         a fixed ladder.  Preserved unchanged so all earlier
//                         experiments keep reproducing.
//   Mode::RaApcWorstCase  RA-APC-Groupcast Strategy A: m_group = min_i m_feas[i]
//   Mode::RaApcP5         RA-APC-Groupcast Strategy B: m_group = P5({m_feas[i]})
//
// In the RA-APC modes the controller also runs a Group-ETP search:
//   ETP(m) = δ(m) · F(m)        (variant=avg)        ← default
//   ETP(m) = δ(m) · Π_i p_i(m)  (variant=strict)
// where δ(m) = floor(pool_PRBs / rbLen(m, payload)) is the multiplexing
// capacity and F(m) is the mean decoding probability over receivers.
//
// Per-RX p_i(m) is estimated from a sigmoid around the 10%-BLER SINR threshold
// for MCS m, using the P5 (worst-slot) SINR from the per-RX SINR window.
//
// Scheduler interaction (timing-correct):
//   SetCurrentMcs() on the scheduler now eagerly pushes the new MCS into
//   every dstInfo it has ever seen — so the next LCP sizing loop picks the
//   right N_subch for the new MCS.

#include "nr-sl-adaptive-mcs-scheduler.h"

#include "ns3/nr-phy-mac-common.h"
#include "ns3/object.h"
#include "ns3/simulator.h"

#include <algorithm>
#include <cmath>
#include <deque>
#include <map>
#include <vector>

namespace ns3
{

class NrSlAdaptiveMcsController : public Object
{
  public:
    enum class Mode
    {
        NackRate,        // legacy
        RaApcWorstCase,  // Strategy A
        RaApcP5,         // Strategy B
    };

    enum class EtpVariant
    {
        Average, // (1/K) Σ p_i(m)
        Strict,  // Π p_i(m)
    };

    static TypeId GetTypeId()
    {
        static TypeId tid =
            TypeId("ns3::NrSlAdaptiveMcsController")
                .SetParent<Object>()
                .AddConstructor<NrSlAdaptiveMcsController>();
        return tid;
    }

    NrSlAdaptiveMcsController() = default;

    // ── Configuration (call before Start()) ──────────────────────────────

    void SetScheduler(Ptr<NrSlAdaptiveMcsScheduler> sched) { m_sched = sched; }

    void SetInitialMcs(uint8_t mcs)
    {
        m_currentMcs = mcs;
        if (m_sched)
            m_sched->SetCurrentMcs(mcs);
    }

    void SetMcsCandidates(std::vector<uint8_t> c) { m_mcs = c; }
    void SetWindowMs(uint32_t ms) { m_windowMs = ms; }
    void SetEvalIntervalMs(uint32_t ms) { m_evalMs = ms; }
    void SetDownThreshold(double t) { m_downThr = t; }
    void SetUpThreshold(double t) { m_upThr = t; }
    void SetUpHysteresis(uint32_t n) { m_upHyst = n; }

    // RA-APC configuration
    void SetMode(Mode m) { m_mode = m; }
    void SetEtpVariant(EtpVariant v) { m_etp = v; }
    void SetPayloadBytes(uint32_t b) { m_payloadBytes = b; }
    void SetPoolPrbs(uint32_t p)     { m_poolPrbs = p; }
    void SetBwPrbs(uint32_t b)       { m_bwPrbs = b; }
    // BLER soft-decision steepness around threshold (1/dB). Default 1.0.
    void SetBlerSlope(double s)      { m_blerSlope = s; }

    // ── Runtime ──────────────────────────────────────────────────────────

    void OnHarqFeedback(std::string /*context*/, const SlHarqInfo& info)
    {
        double nowMs = Simulator::Now().GetMilliSeconds();
        m_wins[info.m_rnti].push({nowMs, info.IsReceivedOk()});
    }

    // Fed from kpi_hooks PSSCH-RX callback. sinrLin is the linear average
    // SINR for the received TB; the caller must convert to dB by 10·log10.
    void OnPsschRxSinr(uint16_t rnti, double sinrDb)
    {
        double nowMs = Simulator::Now().GetMilliSeconds();
        m_sinrWins[rnti].push({nowMs, sinrDb});
    }

    void Start()
    {
        if (m_mcs.empty())
            m_mcs = {28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 7, 6, 5, 4, 3, 2, 1, 0}; // default ladder: MCS 28..0
        Simulator::Schedule(MilliSeconds(m_evalMs),
                            &NrSlAdaptiveMcsController::Tick,
                            this);
    }

    uint8_t CurrentMcs() const { return m_currentMcs; }
    Mode    GetMode() const    { return m_mode; }

  private:
    // ── feedback windows ────────────────────────────────────────────────

    struct Fb   { double ms; bool   ack; };
    struct Sin  { double ms; double db;  };

    template <typename T>
    struct Win
    {
        std::deque<T> q;
        void push(T v) { q.push_back(v); }
        void trim(double nowMs, double winMs)
        {
            while (!q.empty() && q.front().ms < nowMs - winMs)
                q.pop_front();
        }
    };

    // ── MCS-SINR threshold LUT (10% BLER, AWGN approx) ──────────────────
    // Matches the LUT used in the diagnostic scripts; calibrated for
    // NR-SL Mode 2 PSSCH (LDPC, table 1, 64-QAM max).
    static double SinrThreshDb(uint8_t mcs)
    {
        static const double T[] = {
            -5.0, -4.0, -3.0, -2.0, -0.5,  1.0,  2.5,  4.0,
             5.5,  7.0,  8.5,  9.5, 11.0, 12.5, 14.0, 15.5,
            17.0, 18.0, 19.5, 20.5, 22.0, 23.5, 25.0, 26.5,
            27.5, 29.0, 30.0, 31.0, 32.0
        };
        if (mcs >= sizeof(T) / sizeof(T[0]))
            return T[sizeof(T) / sizeof(T[0]) - 1];
        return T[mcs];
    }

    // ── TBS / multiplexing table (μ=1, 12 PSSCH data symbols) ──────────
    // Returns the minimum number of subchannels needed to carry the
    // payload at this MCS. Cached on first use.
    uint32_t RbLenForMcs(uint8_t mcs) const
    {
        auto it = m_rbLenCache.find(mcs);
        if (it != m_rbLenCache.end())
            return it->second;
        // Modulation order × code rate × 1024 for MCS table 1 indexes 0..28
        static const int Qm[]  = {2,2,2,2,2,2,2,2,2,2,4,4,4,4,4,4,4,
                                  6,6,6,6,6,6,6,6,6,6,6,6};
        static const int R1024[] = {120,157,193,251,308,379,449,526,602,679,
                                    340,378,434,490,567,616,666,
                                    438,466,517,567,616,666,719,772,822,873,910,948};
        const int qm = (mcs < 29) ? Qm[mcs] : 6;
        const int r  = (mcs < 29) ? R1024[mcs] : 948;
        const uint32_t payloadBits = m_payloadBytes * 8;
        const uint32_t maxSubch    = std::max(1u, m_poolPrbs / std::max(1u, m_bwPrbs));
        // With PSFCH on (period=1/2/4), the parent's sizing loop assumes
        // 9 PSSCH symbols/slot (hardcoded in nr-sl-ue-mac-scheduler-fixed-mcs.cc).
        // So we mirror that here: 9 symbols × 12 RE/sym/PRB = 108 RE/PRB.
        const uint32_t reRerPrb = 9u * 12u;
        uint32_t pick = maxSubch; // fallback
        for (uint32_t n = 1; n <= maxSubch; ++n)
        {
            const uint32_t nPrb = n * m_bwPrbs;
            const uint32_t nRe  = reRerPrb * nPrb;
            const double   nInfo = double(nRe) * qm * r / 1024.0;
            // Bypass the full 38.214 quantization — using N_info directly
            // is an over-estimate (~5%), safe for "does it fit?" feasibility.
            if (nInfo >= payloadBits)
            {
                pick = n;
                break;
            }
        }
        m_rbLenCache[mcs] = pick;
        return pick;
    }

    // Multiplexing capacity at this MCS: how many concurrent TXs fit.
    uint32_t MultiplexDelta(uint8_t mcs) const
    {
        const uint32_t nSub = RbLenForMcs(mcs);
        if (nSub == 0)
            return 0;
        return std::max(1u, m_poolPrbs / (nSub * std::max(1u, m_bwPrbs)));
    }

    // Sigmoid soft decoding probability around the 10% BLER point.
    // p = 0.9 at threshold; rises to ~1 at +5 dB, falls to ~0 at −5 dB.
    double DecodingProb(double sinrDb, uint8_t mcs) const
    {
        const double thresh = SinrThreshDb(mcs);
        // Shift to make p≈0.9 at threshold:
        // logistic(x) = 1/(1+e^-x); we want logistic(0) = 0.9 → offset ≈ 2.197.
        const double x = m_blerSlope * (sinrDb - thresh) + 2.197;
        return 1.0 / (1.0 + std::exp(-x));
    }

    uint8_t MaxFeasibleMcs(double p5SinrDb) const
    {
        uint8_t best = 0;
        for (int m = 0; m < 29; ++m)
        {
            if (p5SinrDb >= SinrThreshDb(static_cast<uint8_t>(m)))
                best = static_cast<uint8_t>(m);
        }
        return best;
    }

    int mcsIdx(uint8_t mcs) const
    {
        for (int i = 0; i < (int)m_mcs.size(); ++i)
            if (m_mcs[i] == mcs)
                return i;
        return (int)m_mcs.size() - 1;
    }

    // ── periodic tick ────────────────────────────────────────────────────

    void Tick()
    {
        switch (m_mode)
        {
            case Mode::NackRate:        EvaluateNackRate();   break;
            case Mode::RaApcWorstCase:
            case Mode::RaApcP5:         EvaluateRaApc();      break;
        }
        Simulator::Schedule(MilliSeconds(m_evalMs),
                            &NrSlAdaptiveMcsController::Tick,
                            this);
    }

    void EvaluateNackRate()
    {
        double nowMs = Simulator::Now().GetMilliSeconds();

        double sum   = 0.0;
        int    count = 0;
        for (auto& [rnti, w] : m_wins)
        {
            w.trim(nowMs, m_windowMs);
            uint32_t a{0}, n{0};
            for (auto& f : w.q) (f.ack ? a : n) += 1;
            if (a + n == 0) continue;
            sum   += double(n) / (a + n);
            count += 1;
        }
        if (count == 0) return;
        double metric = sum / count;

        uint8_t prev = m_currentMcs;
        if (metric > m_downThr)
        {
            int idx = mcsIdx(m_currentMcs);
            if (idx + 1 < (int)m_mcs.size())
                m_currentMcs = m_mcs[idx + 1];
            m_upCount = 0;
        }
        else if (metric < m_upThr)
        {
            if (++m_upCount >= m_upHyst)
            {
                int idx = mcsIdx(m_currentMcs);
                if (idx > 0)
                    m_currentMcs = m_mcs[idx - 1];
                m_upCount = 0;
            }
        }
        else
        {
            m_upCount = 0;
        }

        if (m_currentMcs != prev && m_sched)
            m_sched->SetCurrentMcs(m_currentMcs);
    }

    void EvaluateRaApc()
    {
        double nowMs = Simulator::Now().GetMilliSeconds();

        // 1. Per-RX P5 SINR → per-RX feasible MCS.
        std::vector<double>  p5_sinr;
        std::vector<uint8_t> feas;
        for (auto& [rnti, w] : m_sinrWins)
        {
            w.trim(nowMs, m_windowMs);
            if (w.q.empty()) continue;
            std::vector<double> vals;
            vals.reserve(w.q.size());
            for (auto& s : w.q) vals.push_back(s.db);
            std::sort(vals.begin(), vals.end());
            // P5: index = ceil(0.05 * N) − 1, clamped to 0.
            size_t idx = vals.size() <= 20
                            ? 0
                            : static_cast<size_t>(0.05 * vals.size());
            double p5 = vals[idx];
            p5_sinr.push_back(p5);
            feas.push_back(MaxFeasibleMcs(p5));
        }
        if (feas.empty())
        {
            // No SINR samples yet — defer; controller will revisit next tick.
            return;
        }

        // 2. Group demand.
        uint8_t groupDemand;
        if (m_mode == Mode::RaApcWorstCase)
        {
            groupDemand = *std::min_element(feas.begin(), feas.end());
        }
        else
        {
            // P5 over per-RX feasible MCS values.
            std::vector<uint8_t> sorted = feas;
            std::sort(sorted.begin(), sorted.end());
            size_t idx = sorted.size() <= 20
                            ? 0
                            : static_cast<size_t>(0.05 * sorted.size());
            groupDemand = sorted[idx];
        }

        // 3. ETP search across the ladder, restricted to m ≤ groupDemand.
        int8_t  bestMcs = -1;
        double  bestEtp = -1.0;
        for (uint8_t m : m_mcs)
        {
            if (m > groupDemand) continue;
            const uint32_t delta = MultiplexDelta(m);
            double f;
            if (m_etp == EtpVariant::Average)
            {
                double s = 0.0;
                for (double sinr : p5_sinr) s += DecodingProb(sinr, m);
                f = s / p5_sinr.size();
            }
            else
            {
                f = 1.0;
                for (double sinr : p5_sinr) f *= DecodingProb(sinr, m);
            }
            const double etp = double(delta) * f;
            if (etp > bestEtp)
            {
                bestEtp = etp;
                bestMcs = static_cast<int8_t>(m);
            }
        }

        if (bestMcs < 0) return;
        const uint8_t newMcs = static_cast<uint8_t>(bestMcs);
        if (newMcs != m_currentMcs && m_sched)
        {
            m_currentMcs = newMcs;
            m_sched->SetCurrentMcs(m_currentMcs);
        }
    }

    // ── state ────────────────────────────────────────────────────────────

    Ptr<NrSlAdaptiveMcsScheduler> m_sched{nullptr};
    Mode                          m_mode{Mode::NackRate};
    EtpVariant                    m_etp{EtpVariant::Average};

    uint8_t                       m_currentMcs{14};
    std::vector<uint8_t>          m_mcs;
    uint32_t                      m_windowMs{500};
    uint32_t                      m_evalMs{500};
    double                        m_downThr{0.15};
    double                        m_upThr{0.08};
    uint32_t                      m_upHyst{2};
    uint32_t                      m_upCount{0};

    // RA-APC params
    uint32_t                      m_payloadBytes{250};
    uint32_t                      m_poolPrbs{100};
    uint32_t                      m_bwPrbs{10};
    double                        m_blerSlope{1.0};

    std::map<uint16_t, Win<Fb>>   m_wins;
    std::map<uint16_t, Win<Sin>>  m_sinrWins;
    mutable std::map<uint8_t, uint32_t> m_rbLenCache;
};

} // namespace ns3
