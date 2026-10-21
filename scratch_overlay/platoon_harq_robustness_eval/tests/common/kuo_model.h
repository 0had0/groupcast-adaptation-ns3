// kuo_model.h -- independent re-implementation of the Kuo et al. 2021 eq. 7/12/13 link model used to validate the helper selector
// (D01 unit test, D02 selection invariants).  Header-only, no ns-3 dependency.  See helper_select.h for the documented model.
#pragma once

#include <algorithm>
#include <cmath>
#include <vector>

namespace ptest
{
namespace kuo
{

// ---- independent link model (documented in helper_select.h, TEST_PLAN section 5) -------------------------------
inline const double kFc = 5.89;     // GHz
inline const double kShLos = 3.0;   // dB
inline const double kShNlos = 4.0;  // dB
inline const double kSigB = 4.5;    // dB

inline double
Phi(double x)
{
    return 0.5 * std::erfc(-x / std::sqrt(2.0));
}

inline double
Clamp(double p)
{
    return std::min(1.0 - 1e-9, std::max(1e-9, p));
}

inline double
PlLos(double d, bool hw)
{
    const double dc = std::max(d, 1.0);
    return hw ? 32.4 + 20.0 * std::log10(dc) + 20.0 * std::log10(kFc)
              : 38.77 + 16.7 * std::log10(dc) + 18.2 * std::log10(kFc);
}

inline double
ProbLos(double d, bool hw)
{
    if (hw)
    {
        return d <= 475.0 ? Clamp(2.1013e-6 * d * d - 0.002 * d + 1.0193) : Clamp(0.54 - 0.001 * (d - 475.0));
    }
    return Clamp(1.05 * std::exp(-0.0114 * d));
}

inline double
PlNlos(double d, bool hw)
{
    const double dc = std::max(d, 1.0);
    return hw ? 32.4 + 30.0 * std::log10(dc) + 20.0 * std::log10(kFc)
              : 36.85 + 30.0 * std::log10(dc) + 18.9 * std::log10(kFc);
}

inline double
PlMax(uint32_t nSubch)
{
    const double nPrb = std::max<double>(1.0, nSubch * 10.0);
    return (23.0 - 10.0 * std::log10(nPrb)) - (-174.0 + 10.0 * std::log10(12 * 30e3) + 9.0) - 8.0;
}

/// P(success) of a link of length d: LOS / NLOSv mixture with P_LOS(d); NLOSv = LOS + blockage B ~ max(0, N(mu, 4.5))
/// integrated with Simpson's rule on 4000 intervals (the selector uses an 80-point midpoint rule).
inline double
PSucc(double d, bool hw, double plMax)
{
    const double pLos = ProbLos(d, hw);
    const double sLos = Phi((plMax - PlLos(d, hw)) / kShLos);
    const double mu = 9.0 + std::max(0.0, 15.0 * std::log10(std::max(d, 1.0)) - 41.0);
    const double hi = std::max(0.0, mu) + 12.0 * kSigB;
    const int n = 4000;
    const double h = hi / n;
    auto f = [&](double b) {
        const double pdf = std::exp(-0.5 * std::pow((b - mu) / kSigB, 2)) / (kSigB * std::sqrt(2.0 * M_PI));
        return pdf * Phi((plMax - PlLos(d, hw) - b) / kShLos);
    };
    double acc = f(0) + f(hi);
    for (int k = 1; k < n; ++k)
    {
        acc += f(k * h) * (k % 2 ? 4.0 : 2.0);
    }
    const double integral = acc * h / 3.0;
    const double sNlosv = Clamp(Phi(-mu / kSigB) * sLos + integral);
    return Clamp(pLos * sLos + (1.0 - pLos) * sNlosv);
}

struct Scores
{
    std::vector<double> g; // member 1..8
    uint32_t chosen;
};

inline Scores
KuoScores(const std::vector<double>& dLeader, const std::vector<std::vector<double>>& dMember, bool hw,
          uint32_t nSubch, bool maxmin, double pResource = 1.0)
{
    const double plMax = PlMax(nSubch);
    const int M = 8;
    std::vector<double> pL(M + 1, 0.0);
    for (int r = 1; r <= M; ++r)
    {
        pL[r] = Clamp(PSucc(dLeader[r], hw, plMax));
    }
    Scores s;
    s.g.assign(M, 0.0);
    double best = -1.0;
    s.chosen = 4;
    for (int i = 1; i <= M; ++i)
    {
        double sum = 0, worst = 1;
        for (int r = 1; r <= M; ++r)
        {
            double ps;
            if (r == i)
            {
                ps = pL[r];
            }
            else
            {
                const double pir = Clamp(pResource * Clamp(PSucc(dMember[i][r], hw, plMax)));
                ps = Clamp(pL[r] + (1.0 - pL[r]) * pL[i] * pir);
            }
            sum += ps;
            worst = std::min(worst, ps);
        }
        const double g = Clamp(maxmin ? worst : sum / M);
        s.g[i - 1] = g;
        if (g > best + 1e-12 || (std::fabs(g - best) <= 1e-12 && pL[i] > pL[s.chosen]))
        {
            best = g;
            s.chosen = i;
        }
    }
    return s;
}


} // namespace kuo
} // namespace ptest
