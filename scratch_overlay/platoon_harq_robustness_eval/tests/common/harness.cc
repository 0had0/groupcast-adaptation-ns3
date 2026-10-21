#include "common/harness.h"

#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <chrono>
#include <climits>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>

#ifndef PTEST_SRC_DIR
#define PTEST_SRC_DIR "."
#endif

namespace ptest
{

const char*
StatusName(Status s)
{
    switch (s)
    {
    case Status::SKIP: return "SKIP";
    case Status::INFO: return "INFO";
    case Status::PASS: return "PASS";
    case Status::XFAIL: return "XFAIL";
    case Status::FAIL: return "FAIL";
    }
    return "?";
}

std::string
Fmt(double v, int prec)
{
    if (std::isnan(v))
    {
        return "nan";
    }
    if (std::isinf(v))
    {
        return v > 0 ? "inf" : "-inf";
    }
    std::ostringstream os;
    os.precision(prec);
    os << v;
    return os.str();
}

std::string
FmtI(long long v)
{
    return std::to_string(v);
}

std::string
FmtCi(double mean, double lo, double hi, int prec)
{
    return Fmt(mean, prec) + " [" + Fmt(lo, prec) + ", " + Fmt(hi, prec) + "]";
}

double
NowS()
{
    using namespace std::chrono;
    return duration<double>(steady_clock::now().time_since_epoch()).count();
}

bool
FileExists(const std::string& p)
{
    struct stat st;
    return ::stat(p.c_str(), &st) == 0 && S_ISREG(st.st_mode);
}

bool
DirExists(const std::string& p)
{
    struct stat st;
    return ::stat(p.c_str(), &st) == 0 && S_ISDIR(st.st_mode);
}

void
MkdirP(const std::string& p)
{
    std::string cur;
    for (size_t i = 0; i < p.size(); ++i)
    {
        cur += p[i];
        if ((p[i] == '/' && i > 0) || i + 1 == p.size())
        {
            ::mkdir(cur.c_str(), 0755);
        }
    }
}

std::string
JsonEscape(const std::string& s)
{
    std::string o;
    o.reserve(s.size() + 8);
    for (unsigned char c : s)
    {
        switch (c)
        {
        case '"': o += "\\\""; break;
        case '\\': o += "\\\\"; break;
        case '\n': o += "\\n"; break;
        case '\r': o += "\\r"; break;
        case '\t': o += "\\t"; break;
        default:
            if (c < 0x20)
            {
                char b[8];
                std::snprintf(b, sizeof b, "\\u%04x", c);
                o += b;
            }
            else
            {
                o += static_cast<char>(c);
            }
        }
    }
    return o;
}

std::string
Shell(const std::string& cmd, int* rc)
{
    std::string out;
    FILE* fp = popen(cmd.c_str(), "r");
    if (!fp)
    {
        if (rc)
        {
            *rc = -1;
        }
        return out;
    }
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, fp)) > 0)
    {
        out.append(buf, n);
    }
    int st = pclose(fp);
    if (rc)
    {
        *rc = WIFEXITED(st) ? WEXITSTATUS(st) : -1;
    }
    while (!out.empty() && (out.back() == '\n' || out.back() == '\r'))
    {
        out.pop_back();
    }
    return out;
}

static std::string
RealPath(const std::string& p)
{
    char buf[PATH_MAX];
    if (::realpath(p.c_str(), buf))
    {
        return std::string(buf);
    }
    return p;
}

std::string
TestsRoot()
{
    if (const char* e = std::getenv("PTEST_ROOT"))
    {
        return RealPath(e);
    }
    return RealPath(PTEST_SRC_DIR);
}

std::string
EvalDir()
{
    return RealPath(TestsRoot() + "/..");
}

std::string
RepoRoot()
{
    return RealPath(TestsRoot() + "/../../..");
}

std::string
ResultsDir()
{
    std::string d = TestsRoot() + "/results";
    MkdirP(d);
    return d;
}

std::string
OutDir()
{
    std::string d = TestsRoot() + "/out";
    MkdirP(d);
    return d;
}

std::string
OutDirV1()
{
    return TestsRoot() + "/out_prefix_v1";
}

std::string
ProdBinary()
{
    return RepoRoot() + "/ns-3-dev/build/scratch/platoon_harq_robustness_eval/"
                        "ns3.42-platoon_harq_robustness_eval-default";
}

void
ViolationLog::Add(const std::string& check, const std::string& detail)
{
    long& c = m_count[check];
    ++c;
    if (static_cast<size_t>(c) <= m_cap)
    {
        m_rows.emplace_back(check, detail);
    }
}

long
ViolationLog::Count(const std::string& check) const
{
    auto it = m_count.find(check);
    return it == m_count.end() ? 0 : it->second;
}

std::string
ViolationLog::Write(const std::string& name) const
{
    if (m_rows.empty())
    {
        return "";
    }
    const std::string rel = "results/" + name + ".csv";
    std::ofstream f(TestsRoot() + "/" + rel);
    f << "check,detail\n";
    for (const auto& r : m_rows)
    {
        std::string d = r.second;
        for (char& ch : d)
        {
            if (ch == ',')
            {
                ch = ';';
            }
        }
        f << r.first << "," << d << "\n";
    }
    return rel;
}

// ---------------------------------------------------------------------------

TestReport::TestReport(std::string id, std::string name, std::string group)
    : m_id(std::move(id)),
      m_name(std::move(name)),
      m_group(std::move(group))
{
    m_t0 = NowS();
}

void
TestReport::AddRunset(const std::string& r)
{
    for (const auto& x : m_runsets)
    {
        if (x == r)
        {
            return;
        }
    }
    m_runsets.push_back(r);
}

void
TestReport::Add(const std::string& name, Status s, Opt o)
{
    m_checks.push_back({name, s, std::move(o)});
    const CheckRec& c = m_checks.back();
    std::cout << "  [" << StatusName(s) << "] " << name;
    if (!c.opt.observed.empty())
    {
        std::cout << "  obs=" << c.opt.observed;
    }
    if (!c.opt.expected.empty())
    {
        std::cout << "  exp=" << c.opt.expected;
    }
    if (!c.opt.finding.empty())
    {
        std::cout << "  (" << c.opt.finding << ")";
    }
    std::cout << "\n";
}

void
TestReport::Check(const std::string& name, bool ok, Opt o)
{
    Add(name, ok ? Status::PASS : Status::FAIL, std::move(o));
}

void
TestReport::CheckXfail(const std::string& name, bool ok, Opt o)
{
    if (o.finding.empty())
    {
        std::cerr << "harness: CheckXfail without finding id: " << name << "\n";
    }
    Add(name, ok ? Status::PASS : Status::XFAIL, std::move(o));
}

void
TestReport::Xfail(const std::string& name, Opt o)
{
    if (o.finding.empty())
    {
        std::cerr << "harness: Xfail without finding id: " << name << "\n";
    }
    Add(name, Status::XFAIL, std::move(o));
}

void
TestReport::Fail(const std::string& name, Opt o)
{
    Add(name, Status::FAIL, std::move(o));
}

void
TestReport::Pass(const std::string& name, Opt o)
{
    Add(name, Status::PASS, std::move(o));
}

void
TestReport::Info(const std::string& name, Opt o)
{
    Add(name, Status::INFO, std::move(o));
}

void
TestReport::Skip(const std::string& name, const std::string& reason, Opt o)
{
    o.note = o.note.empty() ? reason : (reason + " | " + o.note);
    Add(name, Status::SKIP, std::move(o));
}

void
TestReport::Metric(const std::string& key, double v)
{
    if (std::isnan(v) || std::isinf(v))
    {
        m_metrics.emplace_back(key, "null");
    }
    else
    {
        m_metrics.emplace_back(key, Fmt(v, 10));
    }
}

void
TestReport::MetricS(const std::string& key, const std::string& v)
{
    m_metrics.emplace_back(key, "\"" + JsonEscape(v) + "\"");
}

void
TestReport::MetricV(const std::string& key, const std::vector<double>& v)
{
    std::string s = "[";
    for (size_t i = 0; i < v.size(); ++i)
    {
        s += (i ? "," : "");
        s += (std::isnan(v[i]) || std::isinf(v[i])) ? std::string("null") : Fmt(v[i], 10);
    }
    s += "]";
    m_metrics.emplace_back(key, s);
}

void
TestReport::MetricRaw(const std::string& key, const std::string& json)
{
    m_metrics.emplace_back(key, json);
}

Status
TestReport::Overall() const
{
    Status worst = Status::SKIP;
    for (const auto& c : m_checks)
    {
        if (static_cast<int>(c.status) > static_cast<int>(worst))
        {
            worst = c.status;
        }
    }
    return worst;
}

size_t
TestReport::Count(Status s) const
{
    size_t n = 0;
    for (const auto& c : m_checks)
    {
        n += (c.status == s) ? 1 : 0;
    }
    return n;
}

int
TestReport::Finish()
{
    const Status st = Overall();
    const double wall = NowS() - m_t0;
    std::ostringstream js;
    js << "{\"id\":\"" << JsonEscape(m_id) << "\",\"name\":\"" << JsonEscape(m_name)
       << "\",\"group\":\"" << JsonEscape(m_group) << "\",\"status\":\"" << StatusName(st)
       << "\",\n \"summary\":\"" << JsonEscape(m_summary) << "\",\"ref\":[";
    for (size_t i = 0; i < m_refs.size(); ++i)
    {
        js << (i ? "," : "") << "\"" << JsonEscape(m_refs[i]) << "\"";
    }
    js << "],\n \"runsets\":[";
    for (size_t i = 0; i < m_runsets.size(); ++i)
    {
        js << (i ? "," : "") << "\"" << JsonEscape(m_runsets[i]) << "\"";
    }
    js << "],\"n_runs\":" << m_nRuns << ",\"wall_s\":" << Fmt(wall, 6) << ",\n \"checks\":[";
    for (size_t i = 0; i < m_checks.size(); ++i)
    {
        const CheckRec& c = m_checks[i];
        js << (i ? ",\n  " : "\n  ") << "{\"name\":\"" << JsonEscape(c.name) << "\",\"status\":\""
           << StatusName(c.status) << "\",\"expected\":\"" << JsonEscape(c.opt.expected)
           << "\",\"observed\":\"" << JsonEscape(c.opt.observed) << "\",\"tolerance\":\""
           << JsonEscape(c.opt.tolerance) << "\",\"severity\":\"" << JsonEscape(c.opt.severity)
           << "\",\"finding\":" << (c.opt.finding.empty() ? "null" : "\"" + JsonEscape(c.opt.finding) + "\"")
           << ",\"ref\":\"" << JsonEscape(c.opt.ref) << "\",\"note\":\"" << JsonEscape(c.opt.note)
           << "\"}";
    }
    js << "],\n \"metrics\":{";
    for (size_t i = 0; i < m_metrics.size(); ++i)
    {
        js << (i ? ",\n  " : "\n  ") << "\"" << JsonEscape(m_metrics[i].first)
           << "\":" << m_metrics[i].second;
    }
    js << "},\n \"artefacts\":[";
    for (size_t i = 0; i < m_artefacts.size(); ++i)
    {
        js << (i ? "," : "") << "\"" << JsonEscape(m_artefacts[i]) << "\"";
    }
    js << "]}\n";
    const std::string path = ResultsDir() + "/" + m_id + "_" + m_name + ".json";
    {
        std::ofstream f(path);
        f << js.str();
    }
    std::cout << m_id << "_" << m_name << ": " << StatusName(st) << "  (FAIL "
              << Count(Status::FAIL) << ", XFAIL " << Count(Status::XFAIL) << ", PASS "
              << Count(Status::PASS) << ", INFO " << Count(Status::INFO) << ", SKIP "
              << Count(Status::SKIP) << ")  wall " << Fmt(wall, 4) << " s -> " << path << "\n";
    if (Count(Status::FAIL) > 0)
    {
        return 1;
    }
    if (m_checks.empty() || Count(Status::SKIP) == m_checks.size())
    {
        return 77;
    }
    return 0;
}

} // namespace ptest
