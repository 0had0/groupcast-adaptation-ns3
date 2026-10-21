#include "common/synth_trace.h"

#include <cmath>
#include <cstdio>
#include <sstream>

namespace ptest
{

namespace
{
constexpr double kLeaderX = 2000.0;
constexpr double kLaneY = -4.8;
constexpr double kDt = 0.1;
constexpr double kRingR = 12.0;

std::vector<std::pair<double, double>>
Line(double spacing, int n = 8)
{
    std::vector<std::pair<double, double>> v;
    for (int k = 1; k <= n; ++k)
    {
        v.emplace_back(-spacing * k, 0.0);
    }
    return v;
}

double
Round3(double v)
{
    return std::round(v * 1000.0) / 1000.0;
}

std::vector<std::pair<double, double>>
Ring(int n, double r = kRingR, double offsetDeg = 10.0)
{
    std::vector<std::pair<double, double>> v;
    const double pi = 3.14159265358979323846;
    for (int k = 0; k < n; ++k)
    {
        const double a = (offsetDeg + 360.0 * k / n) * pi / 180.0;
        v.emplace_back(Round3(r * std::cos(a)), Round3(r * std::sin(a)));
    }
    return v;
}

std::string
F(const char* fmt, double v)
{
    char b[64];
    std::snprintf(b, sizeof b, fmt, v);
    return b;
}
} // namespace

std::vector<std::string>
SynthGeometries()
{
    return {"SYN_NEAR", "SYN_MARGINAL", "SYN_DTX",     "SYN_MCR",     "SYN_DIST_10",
            "SYN_DIST_20", "SYN_DIST_30", "SYN_DIST_45", "SYN_DIST_60", "SYN_DIST_80",
            "SYN_SPS"};
}

std::vector<std::pair<double, double>>
SynthMembers(const std::string& g)
{
    if (g == "SYN_NEAR")
    {
        return Ring(8);
    }
    if (g == "SYN_MARGINAL" || g == "SYN_SPS")
    {
        return Line(60);
    }
    if (g == "SYN_DTX" || g == "SYN_MCR")
    {
        auto v = Ring(7);
        v.emplace_back(g == "SYN_DTX" ? -3000.0 : -650.0, 0.0);
        return v;
    }
    if (g.rfind("SYN_DIST_", 0) == 0)
    {
        return Line(std::stod(g.substr(9)));
    }
    return {};
}

std::string
RenderSynthTrace(const std::string& geom, double tEnd, double kpiStart)
{
    const auto members = SynthMembers(geom);
    std::ostringstream os;
    const double spacing = members.empty() ? 0.0 : std::hypot(members[0].first, members[0].second);
    os << "# scenario=highway\n"
       << "# ivd_m=" << F("%.1f", std::max(spacing - 5.0, 0.0)) << "\n"
       << "# veh_len_m=5.0\n# platoon=9\n# nbg_target=0\n# seats=0\n# bg_in_area_mean=0\n"
       << "# bg_in_area_min=0\n# bg_in_area_max=0\n"
       << "# t_end=" << F("%.1f", tEnd) << "\n# kpi_start=" << F("%.1f", kpiStart) << "\n"
       << "# dt=0.1\n# seed=1\n# platoon_speed_mps=0.0\n# synthetic=" << geom << "\n"
       << "time,node_id,x,y,speed\n";
    std::vector<std::pair<double, double>> pts;
    pts.emplace_back(kLeaderX, kLaneY);
    for (const auto& m : members)
    {
        pts.emplace_back(kLeaderX + m.first, kLaneY + m.second);
    }
    const int steps = static_cast<int>(std::lround(tEnd / kDt));
    for (int s = 0; s <= steps; ++s)
    {
        const double t = s * kDt;
        for (size_t n = 0; n < pts.size(); ++n)
        {
            os << F("%.3f", t) << "," << n << "," << F("%.3f", pts[n].first) << ","
               << F("%.3f", pts[n].second) << ",0.000\n";
        }
    }
    return os.str();
}

} // namespace ptest
