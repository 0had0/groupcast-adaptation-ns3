#include "common/stats.h"

#include <algorithm>
#include <numeric>
#include <random>

namespace ptest
{

double
Sum(const std::vector<double>& v)
{
    double s = 0;
    for (double x : v)
    {
        s += x;
    }
    return s;
}

double
Mean(const std::vector<double>& v)
{
    return v.empty() ? NAN : Sum(v) / static_cast<double>(v.size());
}

double
Sd(const std::vector<double>& v)
{
    if (v.size() < 2)
    {
        return NAN;
    }
    const double m = Mean(v);
    double s = 0;
    for (double x : v)
    {
        s += (x - m) * (x - m);
    }
    return std::sqrt(s / static_cast<double>(v.size() - 1));
}

double
Quantile(std::vector<double> v, double q)
{
    if (v.empty())
    {
        return NAN;
    }
    std::sort(v.begin(), v.end());
    const double pos = q * static_cast<double>(v.size() - 1);
    const size_t i = static_cast<size_t>(std::floor(pos));
    const size_t j = std::min(v.size() - 1, i + 1);
    const double f = pos - static_cast<double>(i);
    return v[i] * (1 - f) + v[j] * f;
}

double
Median(std::vector<double> v)
{
    return Quantile(std::move(v), 0.5);
}

// ---- special functions ------------------------------------------------------

static double
BetaCf(double a, double b, double x)
{
    const int maxIt = 300;
    const double eps = 3e-14;
    const double fpmin = 1e-300;
    double qab = a + b, qap = a + 1.0, qam = a - 1.0;
    double c = 1.0;
    double d = 1.0 - qab * x / qap;
    if (std::fabs(d) < fpmin)
    {
        d = fpmin;
    }
    d = 1.0 / d;
    double h = d;
    for (int m = 1; m <= maxIt; ++m)
    {
        int m2 = 2 * m;
        double aa = m * (b - m) * x / ((qam + m2) * (a + m2));
        d = 1.0 + aa * d;
        if (std::fabs(d) < fpmin) d = fpmin;
        c = 1.0 + aa / c;
        if (std::fabs(c) < fpmin) c = fpmin;
        d = 1.0 / d;
        h *= d * c;
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2));
        d = 1.0 + aa * d;
        if (std::fabs(d) < fpmin) d = fpmin;
        c = 1.0 + aa / c;
        if (std::fabs(c) < fpmin) c = fpmin;
        d = 1.0 / d;
        double del = d * c;
        h *= del;
        if (std::fabs(del - 1.0) < eps)
        {
            break;
        }
    }
    return h;
}

double
IncBeta(double a, double b, double x)
{
    if (x <= 0)
    {
        return 0;
    }
    if (x >= 1)
    {
        return 1;
    }
    const double lbt = std::lgamma(a + b) - std::lgamma(a) - std::lgamma(b) + a * std::log(x) +
                       b * std::log(1.0 - x);
    const double bt = std::exp(lbt);
    if (x < (a + 1.0) / (a + b + 2.0))
    {
        return bt * BetaCf(a, b, x) / a;
    }
    return 1.0 - bt * BetaCf(b, a, 1.0 - x) / b;
}

double
TCdf(double t, double df)
{
    const double x = df / (df + t * t);
    const double ib = IncBeta(df / 2.0, 0.5, x);
    return t >= 0 ? 1.0 - 0.5 * ib : 0.5 * ib;
}

double
TQuantile2(double conf, double df)
{
    // P(|T| <= q) = conf  <=>  CDF(q) = (1 + conf)/2
    const double target = 0.5 * (1.0 + conf);
    double lo = 0.0, hi = 1.0;
    while (TCdf(hi, df) < target && hi < 1e8)
    {
        hi *= 2.0;
    }
    for (int i = 0; i < 200; ++i)
    {
        double mid = 0.5 * (lo + hi);
        if (TCdf(mid, df) < target)
        {
            lo = mid;
        }
        else
        {
            hi = mid;
        }
    }
    return 0.5 * (lo + hi);
}

// regularised upper incomplete gamma Q(a, x)
static double
GammaQ(double a, double x)
{
    if (x <= 0)
    {
        return 1.0;
    }
    const double gln = std::lgamma(a);
    if (x < a + 1.0)
    {
        // series for P
        double ap = a, sum = 1.0 / a, del = sum;
        for (int n = 0; n < 1000; ++n)
        {
            ap += 1.0;
            del *= x / ap;
            sum += del;
            if (std::fabs(del) < std::fabs(sum) * 1e-14)
            {
                break;
            }
        }
        return 1.0 - sum * std::exp(-x + a * std::log(x) - gln);
    }
    // continued fraction for Q
    const double fpmin = 1e-300;
    double b = x + 1.0 - a, c = 1.0 / fpmin, d = 1.0 / b, h = d;
    for (int i = 1; i < 1000; ++i)
    {
        double an = -i * (i - a);
        b += 2.0;
        d = an * d + b;
        if (std::fabs(d) < fpmin) d = fpmin;
        c = b + an / c;
        if (std::fabs(c) < fpmin) c = fpmin;
        d = 1.0 / d;
        double del = d * c;
        h *= del;
        if (std::fabs(del - 1.0) < 1e-14)
        {
            break;
        }
    }
    return std::exp(-x + a * std::log(x) - gln) * h;
}

double
ChiSqSf(double x, double df)
{
    return GammaQ(df / 2.0, x / 2.0);
}

double
NormalCdf(double z)
{
    return 0.5 * std::erfc(-z / std::sqrt(2.0));
}

// ---- estimators ---------------------------------------------------------------

MeanCI
MeanCi(const std::vector<double>& v, double conf)
{
    MeanCI r;
    r.n = v.size();
    r.mean = Mean(v);
    if (r.n < 2)
    {
        return r;
    }
    r.sd = Sd(v);
    const double se = r.sd / std::sqrt(static_cast<double>(r.n));
    const double q = TQuantile2(conf, static_cast<double>(r.n - 1));
    r.lo = r.mean - q * se;
    r.hi = r.mean + q * se;
    return r;
}

Prop
Wilson(long k, long n, double z)
{
    Prop r;
    r.k = k;
    r.n = n;
    if (n <= 0)
    {
        return r;
    }
    const double p = static_cast<double>(k) / static_cast<double>(n);
    const double nn = static_cast<double>(n);
    const double den = 1.0 + z * z / nn;
    const double cen = (p + z * z / (2 * nn)) / den;
    const double hw = z * std::sqrt(p * (1 - p) / nn + z * z / (4 * nn * nn)) / den;
    r.p = p;
    r.lo = std::max(0.0, cen - hw);
    r.hi = std::min(1.0, cen + hw);
    return r;
}

std::vector<double>
Ranks(const std::vector<double>& v)
{
    const size_t n = v.size();
    std::vector<size_t> o(n);
    std::iota(o.begin(), o.end(), 0);
    std::stable_sort(o.begin(), o.end(), [&](size_t a, size_t b) { return v[a] < v[b]; });
    std::vector<double> r(n);
    size_t i = 0;
    while (i < n)
    {
        size_t j = i;
        while (j + 1 < n && v[o[j + 1]] == v[o[i]])
        {
            ++j;
        }
        const double avg = 0.5 * static_cast<double>(i + j) + 1.0;
        for (size_t k = i; k <= j; ++k)
        {
            r[o[k]] = avg;
        }
        i = j + 1;
    }
    return r;
}

Corr
Spearman(const std::vector<double>& x, const std::vector<double>& y)
{
    Corr c;
    const size_t n = std::min(x.size(), y.size());
    c.n = n;
    if (n < 3)
    {
        return c;
    }
    std::vector<double> xs(x.begin(), x.begin() + n), ys(y.begin(), y.begin() + n);
    const std::vector<double> rx = Ranks(xs), ry = Ranks(ys);
    const double mx = Mean(rx), my = Mean(ry);
    double sxy = 0, sxx = 0, syy = 0;
    for (size_t i = 0; i < n; ++i)
    {
        sxy += (rx[i] - mx) * (ry[i] - my);
        sxx += (rx[i] - mx) * (rx[i] - mx);
        syy += (ry[i] - my) * (ry[i] - my);
    }
    if (sxx <= 0 || syy <= 0)
    {
        return c; // constant series: undefined
    }
    c.rho = sxy / std::sqrt(sxx * syy);
    const double r = std::max(-1.0, std::min(1.0, c.rho));
    if (std::fabs(r) >= 1.0 - 1e-12)
    {
        c.p = 0.0;
    }
    else
    {
        const double t = r * std::sqrt((static_cast<double>(n) - 2.0) / (1.0 - r * r));
        c.p = 2.0 * (1.0 - TCdf(std::fabs(t), static_cast<double>(n) - 2.0));
    }
    return c;
}

bool
DirectionalOk(const Corr& c, int expectedSign)
{
    if (std::isnan(c.rho))
    {
        return false;
    }
    const bool signOk = (expectedSign > 0) ? c.rho > 0 : c.rho < 0;
    return signOk && (c.p < 0.05 || std::fabs(c.rho) >= 0.6);
}

double
BinomTwoSided(long k, long n, double p0)
{
    if (n <= 0)
    {
        return NAN;
    }
    auto logPmf = [&](long i) {
        return std::lgamma(n + 1.0) - std::lgamma(i + 1.0) - std::lgamma(n - i + 1.0) +
               (p0 > 0 ? i * std::log(p0) : (i == 0 ? 0.0 : -1e300)) +
               (p0 < 1 ? (n - i) * std::log(1 - p0) : (n - i == 0 ? 0.0 : -1e300));
    };
    const double pk = logPmf(k);
    double p = 0;
    for (long i = 0; i <= n; ++i)
    {
        const double lp = logPmf(i);
        if (lp <= pk + 1e-9)
        {
            p += std::exp(lp);
        }
    }
    return std::min(1.0, p);
}

DiffCI
Paired(const std::vector<double>& a, const std::vector<double>& b)
{
    DiffCI r;
    const size_t n = std::min(a.size(), b.size());
    r.n = n;
    std::vector<double> d(n);
    for (size_t i = 0; i < n; ++i)
    {
        d[i] = a[i] - b[i];
        r.nPos += d[i] > 0 ? 1 : 0;
        r.nNeg += d[i] < 0 ? 1 : 0;
    }
    r.diff = Mean(d);
    if (n < 2)
    {
        return r;
    }
    const MeanCI m = MeanCi(d);
    r.lo = m.lo;
    r.hi = m.hi;
    const double se = m.sd / std::sqrt(static_cast<double>(n));
    if (se > 0)
    {
        const double t = m.mean / se;
        r.p = 2.0 * (1.0 - TCdf(std::fabs(t), static_cast<double>(n - 1)));
    }
    else
    {
        r.p = (m.mean == 0) ? 1.0 : 0.0;
    }
    if (r.nPos + r.nNeg > 0)
    {
        r.signP = BinomTwoSided(r.nPos, r.nPos + r.nNeg, 0.5);
    }
    return r;
}

DiffCI
Welch(const std::vector<double>& a, const std::vector<double>& b)
{
    DiffCI r;
    r.n = std::min(a.size(), b.size());
    if (a.size() < 2 || b.size() < 2)
    {
        r.diff = Mean(a) - Mean(b);
        return r;
    }
    const double ma = Mean(a), mb = Mean(b);
    const double va = Sd(a) * Sd(a) / static_cast<double>(a.size());
    const double vb = Sd(b) * Sd(b) / static_cast<double>(b.size());
    r.diff = ma - mb;
    const double se = std::sqrt(va + vb);
    if (se <= 0)
    {
        r.lo = r.hi = r.diff;
        r.p = r.diff == 0 ? 1.0 : 0.0;
        return r;
    }
    const double df = (va + vb) * (va + vb) /
                      (va * va / static_cast<double>(a.size() - 1) +
                       vb * vb / static_cast<double>(b.size() - 1));
    const double q = TQuantile2(0.95, df);
    r.lo = r.diff - q * se;
    r.hi = r.diff + q * se;
    r.p = 2.0 * (1.0 - TCdf(std::fabs(r.diff / se), df));
    return r;
}

Boot
BootstrapMean(const std::vector<double>& d, int B, unsigned seed)
{
    Boot r;
    if (d.empty())
    {
        return r;
    }
    std::mt19937 rng(seed);
    std::uniform_int_distribution<size_t> pick(0, d.size() - 1);
    std::vector<double> means(B);
    for (int b = 0; b < B; ++b)
    {
        double s = 0;
        for (size_t i = 0; i < d.size(); ++i)
        {
            s += d[pick(rng)];
        }
        means[b] = s / static_cast<double>(d.size());
    }
    r.mean = Mean(d);
    r.lo = Quantile(means, 0.025);
    r.hi = Quantile(means, 0.975);
    return r;
}

Gof
ChiSqGof(const std::vector<double>& obs, const std::vector<double>& prob)
{
    Gof g;
    const double n = Sum(obs);
    double stat = 0;
    long cells = 0;
    for (size_t i = 0; i < obs.size() && i < prob.size(); ++i)
    {
        const double e = n * prob[i];
        if (e <= 0)
        {
            continue;
        }
        stat += (obs[i] - e) * (obs[i] - e) / e;
        ++cells;
    }
    g.stat = stat;
    g.df = cells - 1;
    g.p = g.df > 0 ? ChiSqSf(stat, static_cast<double>(g.df)) : NAN;
    return g;
}

Gof
KsTest(std::vector<double> s, const std::function<double(double)>& cdf)
{
    Gof g;
    const size_t n = s.size();
    if (n == 0)
    {
        return g;
    }
    std::sort(s.begin(), s.end());
    double d = 0;
    for (size_t i = 0; i < n; ++i)
    {
        const double f = cdf(s[i]);
        d = std::max(d, std::max(std::fabs(f - static_cast<double>(i) / n),
                                 std::fabs(f - static_cast<double>(i + 1) / n)));
    }
    g.stat = d;
    g.df = static_cast<long>(n);
    const double sn = std::sqrt(static_cast<double>(n));
    const double lam = (sn + 0.12 + 0.11 / sn) * d;
    double p = 0;
    for (int k = 1; k <= 100; ++k)
    {
        const double term = 2.0 * ((k % 2) ? 1.0 : -1.0) * std::exp(-2.0 * k * k * lam * lam);
        p += term;
        if (std::fabs(term) < 1e-12)
        {
            break;
        }
    }
    g.p = std::max(0.0, std::min(1.0, p));
    return g;
}

} // namespace ptest
