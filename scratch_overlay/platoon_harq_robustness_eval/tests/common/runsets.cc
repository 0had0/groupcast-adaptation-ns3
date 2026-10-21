#include "common/runsets.h"

#include "common/csv.h"
#include "common/harness.h"

#include <algorithm>

namespace ptest
{

std::string
RunRef::Retx() const
{
    if (arm.size() >= 2 && arm[0] == 'R' && arm[1] >= '1' && arm[1] <= '6')
    {
        return arm.substr(0, 2);
    }
    return "";
}

std::string
RunRef::Sel() const
{
    const auto p = arm.find("_H");
    if (p == std::string::npos)
    {
        return "";
    }
    return arm.substr(p + 1, 2);
}

std::string
RunRef::HarqMode() const
{
    const std::string r = Retx();
    if (r == "R1" || r == "R4")
    {
        return "blind";
    }
    if (r == "R2" || r == "R5")
    {
        return "opt1";
    }
    if (r == "R3" || r == "R6")
    {
        return "opt2";
    }
    return "";
}

std::string
RunRef::Counterpart() const
{
    const std::string r = Retx();
    if (r == "R4") return "R1";
    if (r == "R5") return "R2";
    if (r == "R6") return "R3";
    return "";
}

std::string
RunRef::CellKey() const
{
    return scenario + "/" + ivd + "/" + bg + "/" + traffic + (leaderSps ? "" : "/sps0");
}

std::vector<RunRef>
LoadManifest(const std::string& runset, const std::string& outDir)
{
    std::vector<RunRef> out;
    Table t;
    if (!LoadTable((outDir.empty() ? OutDir() : outDir) + "/" + runset + "/manifest.csv", t))
    {
        return out;
    }
    for (size_t i = 0; i < t.nrows; ++i)
    {
        RunRef r;
        r.runset = t.StrAt("runset", i);
        r.arm = t.StrAt("arm", i);
        r.scenario = t.StrAt("scenario", i);
        r.ivd = t.StrAt("ivd", i);
        r.bg = t.StrAt("bg", i);
        r.traffic = t.StrAt("traffic", i);
        r.tag = t.StrAt("tag", i);
        r.prefix = outDir.empty() ? t.StrAt("out_prefix", i) : outDir + "/" + runset + "/" + r.arm + "/" + r.tag;
        r.cmd = t.StrAt("cmd", i);
        r.seed = static_cast<int>(t.At("seed", i));
        r.leaderSps = t.At("leader_sps", i) != 0;
        r.exitCode = static_cast<int>(t.At("exit", i));
        r.wallS = t.At("wall_s", i);
        r.maxRssMb = t.At("max_rss_mb", i);
        if (r.exitCode == 0)
        {
            out.push_back(std::move(r));
        }
    }
    return out;
}

std::vector<RunRef>
LoadManifests(const std::vector<std::string>& runsets)
{
    std::vector<RunRef> out;
    for (const auto& s : runsets)
    {
        auto v = LoadManifest(s);
        out.insert(out.end(), v.begin(), v.end());
    }
    return out;
}

std::vector<RunRef>
Select(const std::vector<RunRef>& v, const std::string& arm, const std::string& traffic,
       const std::string& bg, const std::string& scenario, const std::string& ivd)
{
    std::vector<RunRef> o;
    for (const auto& r : v)
    {
        if ((arm.empty() || r.arm == arm) && (traffic.empty() || r.traffic == traffic) &&
            (bg.empty() || r.bg == bg) && (scenario.empty() || r.scenario == scenario) &&
            (ivd.empty() || r.ivd == ivd))
        {
            o.push_back(r);
        }
    }
    return o;
}

const std::vector<std::string>&
StudyArms()
{
    static const std::vector<std::string> a = {"R1", "R2", "R3", "R4_S1_H2", "R5_S1_H2", "R6_S1_H2"};
    return a;
}

double
PdbSeconds(const std::string& traffic)
{
    if (traffic.rfind("periodic1", 0) == 0)
    {
        return 0.1;
    }
    if (traffic.rfind("periodic2", 0) == 0)
    {
        return 0.01;
    }
    return 0.05;
}

double
RriMsOfTraffic(const std::string& traffic)
{
    if (traffic.rfind("periodic1", 0) == 0)
    {
        return 100.0;
    }
    if (traffic.rfind("periodic2", 0) == 0)
    {
        return 10.0;
    }
    return 20.0; // aperiodic1tb with --aperiodicRri=20
}

} // namespace ptest
