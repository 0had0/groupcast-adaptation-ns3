// stats.h -- statistics used by the checks (TEST_PLAN section 7.3): mean +- t-CI, Wilson, Spearman rho + p,
// Welch, paired difference + sign test, paired bootstrap, exact binomial, KS and chi-square.
#pragma once

#include <cmath>
#include <cstddef>
#include <functional>
#include <vector>

namespace ptest
{

double Mean(const std::vector<double>& v);
double Sd(const std::vector<double>& v); //!< sample standard deviation (n-1), NaN for n < 2
double Median(std::vector<double> v);
double Quantile(std::vector<double> v, double q); //!< linear interpolation, q in [0,1]
double Sum(const std::vector<double>& v);

/// regularised incomplete beta I_x(a,b)
double IncBeta(double a, double b, double x);
/// Student t CDF
double TCdf(double t, double df);
/// two-sided t quantile: P(|T| <= q) = conf
double TQuantile2(double conf, double df);
/// upper tail of chi-square with df degrees of freedom
double ChiSqSf(double x, double df);
double NormalCdf(double z);

struct MeanCI
{
    double mean{NAN}, lo{NAN}, hi{NAN}, sd{NAN};
    size_t n{0};
    bool ok() const { return n >= 2; }
    /// CI excludes zero
    bool ExcludesZero() const { return ok() && (lo > 0 || hi < 0); }
    bool IncludesZero() const { return ok() && lo <= 0 && hi >= 0; }
};
MeanCI MeanCi(const std::vector<double>& v, double conf = 0.95);

struct Prop
{
    double p{NAN}, lo{NAN}, hi{NAN};
    long k{0}, n{0};
};
Prop Wilson(long k, long n, double z = 1.959964);

struct Corr
{
    double rho{NAN}, p{NAN};
    size_t n{0};
};
Corr Spearman(const std::vector<double>& x, const std::vector<double>& y);
/// directional check of section 7.3: expected sign (+1/-1); true if sign correct and (p<0.05 or |rho|>=0.6)
bool DirectionalOk(const Corr& c, int expectedSign);

struct DiffCI
{
    double diff{NAN}, lo{NAN}, hi{NAN}, p{NAN}, signP{NAN};
    size_t n{0};
    int nPos{0}, nNeg{0};
    bool ok() const { return n >= 2; }
    bool ExcludesZero() const { return ok() && (lo > 0 || hi < 0); }
    bool IncludesZero() const { return ok() && lo <= 0 && hi >= 0; }
};
/// paired a-b (equal length), t-CI and sign test
DiffCI Paired(const std::vector<double>& a, const std::vector<double>& b);
/// Welch a-b
DiffCI Welch(const std::vector<double>& a, const std::vector<double>& b);

double BinomTwoSided(long k, long n, double p0);

struct Boot
{
    double mean{NAN}, lo{NAN}, hi{NAN};
};
Boot BootstrapMean(const std::vector<double>& d, int B = 2000, unsigned seed = 12345);

struct Gof
{
    double stat{NAN}, p{NAN};
    long df{0};
};
/// chi-square test of counts against given probabilities (sum 1)
Gof ChiSqGof(const std::vector<double>& obs, const std::vector<double>& prob);
/// one-sample KS test against a CDF
Gof KsTest(std::vector<double> sample, const std::function<double(double)>& cdf);

/// Average ranks (ties share the mean rank)
std::vector<double> Ranks(const std::vector<double>& v);

} // namespace ptest
