// harness.h -- check bookkeeping, JSON result writer and exit codes of the platoon_harq_robustness_eval
// test suite (see tests/TEST_PLAN.md section 7.2).
//
// Exit codes: 0 = no FAIL, 1 = at least one FAIL, 77 = every check SKIP (or no check at all).
// Test status = worst of its checks: FAIL > XFAIL > PASS > INFO > SKIP.
#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace ptest
{

enum class Status
{
    SKIP = 0,
    INFO = 1,
    PASS = 2,
    XFAIL = 3,
    FAIL = 4
};

const char* StatusName(Status s);

/// Optional descriptors of a check.
struct Opt
{
    std::string expected;
    std::string observed;
    std::string tolerance;
    std::string severity; //!< H | M | L | ""
    std::string finding;  //!< F01..F29 / DV1..DV10 or ""
    std::string ref;
    std::string note;

    Opt& Exp(const std::string& s) { expected = s; return *this; }
    Opt& Obs(const std::string& s) { observed = s; return *this; }
    Opt& Tol(const std::string& s) { tolerance = s; return *this; }
    Opt& Sev(const std::string& s) { severity = s; return *this; }
    Opt& Fnd(const std::string& s) { finding = s; return *this; }
    Opt& Ref(const std::string& s) { ref = s; return *this; }
    Opt& Note(const std::string& s) { note = s; return *this; }
};

struct CheckRec
{
    std::string name;
    Status status;
    Opt opt;
};

/// Format helpers (shared by all tests)
std::string Fmt(double v, int prec = 6);
std::string FmtI(long long v);
std::string FmtCi(double mean, double lo, double hi, int prec = 5);

class TestReport
{
  public:
    TestReport(std::string id, std::string name, std::string group);

    void Summary(const std::string& s) { m_summary = s; }
    void AddRef(const std::string& r) { m_refs.push_back(r); }
    void AddRunset(const std::string& r);
    void SetNRuns(long n) { m_nRuns = n; }
    void AddNRuns(long n) { m_nRuns += n; }

    /// PASS if ok, else FAIL
    void Check(const std::string& name, bool ok, Opt o = Opt());
    /// PASS if ok, else XFAIL tied to a finding / deviation (o.finding must be set; the note carries evidence)
    void CheckXfail(const std::string& name, bool ok, Opt o);
    /// A confirmed finding: always XFAIL (o.finding set, note with evidence)
    void Xfail(const std::string& name, Opt o);
    void Fail(const std::string& name, Opt o = Opt());
    void Pass(const std::string& name, Opt o = Opt());
    void Info(const std::string& name, Opt o = Opt());
    void Skip(const std::string& name, const std::string& reason, Opt o = Opt());

    void Metric(const std::string& key, double v);
    void MetricS(const std::string& key, const std::string& v);
    void MetricV(const std::string& key, const std::vector<double>& v);
    /// raw JSON fragment (object / array) stored under metrics.<key>
    void MetricRaw(const std::string& key, const std::string& json);
    void Artefact(const std::string& relPath) { m_artefacts.push_back(relPath); }

    Status Overall() const;
    size_t Count(Status s) const;
    const std::vector<CheckRec>& Checks() const { return m_checks; }

    /// Writes results/<id>_<name>.json, prints a summary, returns the process exit code.
    int Finish();

    const std::string& Id() const { return m_id; }
    const std::string& Name() const { return m_name; }

  private:
    void Add(const std::string& name, Status s, Opt o);

    std::string m_id, m_name, m_group, m_summary;
    std::vector<std::string> m_refs, m_runsets, m_artefacts;
    long m_nRuns{0};
    double m_t0{0};
    std::vector<CheckRec> m_checks;
    std::vector<std::pair<std::string, std::string>> m_metrics; // key -> JSON value text
};

/// Collects up to `cap` offending rows per check (TEST_PLAN 7.3: "list up to 20 offending rows in the artefact CSV")
class ViolationLog
{
  public:
    explicit ViolationLog(size_t cap = 20) : m_cap(cap) {}
    void Add(const std::string& check, const std::string& detail);
    long Count(const std::string& check) const;
    /// writes results/<name>.csv (check,detail) if anything was recorded; returns the relative path or ""
    std::string Write(const std::string& name) const;

  private:
    size_t m_cap;
    std::map<std::string, long> m_count;
    std::vector<std::pair<std::string, std::string>> m_rows;
};

// ---- paths ------------------------------------------------------------------
/// root of the tests directory (scratch_overlay/platoon_harq_robustness_eval/tests); env PTEST_ROOT overrides
std::string TestsRoot();
std::string ResultsDir();
std::string OutDir();
/// tests/out_prefix_v1: the pre-fix outputs (read-only reference for before/after comparisons)
std::string OutDirV1();
/// production binary
std::string ProdBinary();
/// scratch_overlay/platoon_harq_robustness_eval
std::string EvalDir();
/// repo root
std::string RepoRoot();

bool FileExists(const std::string& p);
bool DirExists(const std::string& p);
void MkdirP(const std::string& p);
std::string JsonEscape(const std::string& s);
double NowS();
/// run a shell command, return stdout (trailing newline stripped); exit code in *rc
std::string Shell(const std::string& cmd, int* rc = nullptr);

} // namespace ptest
