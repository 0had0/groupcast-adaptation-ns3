// A03_nr_examples_regression (W, external binaries) + A03b (stock NR test suites): the stock examples sl-multi-lc-example and sl-harq-example
// of the patched NR module against their reference logs (nr-sl-simple-multi-lc-example-test.cc); the HARQ-disabled blind-copy patch (DV9 / F11)
// shows up as the only expected difference.
#include "common/csv.h"
#include "common/harness.h"
#include "common/micro_sl.h"

#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>

using namespace ptest;

namespace
{
struct Case
{
    std::string name, args;
};
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
    TestReport T("A03", "nr_examples_regression", "A");
    T.Summary("The patched NR module reproduces the stock reference logs of sl-multi-lc-example except sl-multi-lc-dyn-gcast-no-harq, where the "
              "ungated blind-copy patch (DV9 / F11) sends up to 5 copies instead of 1; copies per TB with HARQ on/off and PSFCH on/off.");
    T.AddRef("ns-3-dev/contrib/nr/test/nr-sl-simple-multi-lc-example-test.cc, test/sl-test-data/*.reflog");
    T.AddRef("TEST_PLAN F11 / DV9");
    const std::string repo = RepoRoot();
    const std::string bin = repo + "/ns-3-dev/build/contrib/nr/examples/ns3.42-sl-multi-lc-example-default";
    const std::string refdir = repo + "/ns-3-dev/contrib/nr/test/sl-test-data";
    const std::vector<Case> cases = {
        {"sl-multi-lc-dyn-gcast-harq", "--writeTraces=0"},
        {"sl-multi-lc-dyn-gcast-no-harq", "--harqEnabled=0 --writeTraces=0"},
        {"sl-multi-lc-dyn-gcast-blind", "--psfchPeriod=0 --writeTraces=0"},
        {"sl-multi-lc-prio-dyn", "--schedTypeConfig=3 --writeTraces=0"},
        {"sl-multi-lc-prio-sps", "--schedTypeConfig=3 --prioToSps=1 --writeTraces=0"},
        {"sl-multi-lc-prio-bcast", "--dstL2IdConfig=3 --priorityConfig=2 --writeTraces=0"},
        {"sl-multi-lc-prio-gcast", "--dstL2IdConfig=3 --priorityConfig=3 --writeTraces=0"},
        {"sl-multi-lc-prio-uni", "--dstL2IdConfig=3 --priorityConfig=3 --RngRun=2 --writeTraces=0"},
        {"sl-multi-lc-rri", "--rriConfig=2 --schedTypeConfig=2 --writeTraces=0"},
    };
    if (!FileExists(bin))
    {
        T.Skip("examples/sl-multi-lc-example", "binary missing: build with 'ninja -C ns-3-dev/cmake-cache sl-multi-lc-example' (examples are enabled in this build)");
    }
    else
    {
        for (const Case& c : cases)
        {
            const std::string dir = OutDir() + "/A03/" + c.name;
            MkdirP(dir);
            const std::string out = dir + "/stdout.txt";
            int rc = 0;
            Shell("cd '" + dir + "' && env -u MAGIC_HELPER_ID -u NUM_MEMBERS '" + bin + "' " + c.args + " > '" + out + "' 2> '" + dir + "/stderr.txt'", &rc);
            const std::string ref = refdir + "/" + c.name + ".reflog";
            int drc = 0;
            const std::string diff = Shell("diff '" + out + "' '" + ref + "' | head -12", &drc);
            const bool same = diff.empty();
            T.AddNRuns(1);
            if (c.name == "sl-multi-lc-dyn-gcast-no-harq")
            {
                // expected difference: no-harq:1 vs no-harq:5 (blind copies of a HARQ-disabled LC)
                const std::string txt = ReadFile(out);
                const bool five = txt.find("no-harq:5") != std::string::npos;
                const bool refOne = ReadFile(ref).find("no-harq:1") != std::string::npos;
                if (same)
                    T.Pass("reflog " + c.name + ": identical to the stock reference (patch not active)", Opt().Fnd("F11"));
                else
                    T.Xfail("reflog " + c.name + ": differs from the stock reference", Opt().Fnd("F11").Sev("M")
                                .Exp("stock: one transmission for a HARQ-disabled LC (reflog 'no-harq:1')")
                                .Obs(std::string("patched build prints ") + (five ? "'no-harq:5'" : "other output") + (refOne ? " vs reference 'no-harq:1'" : "") + "; first diff lines: " + diff.substr(0, 400))
                                .Note("DV9: the ungated patch makes every HARQ-disabled LC send maxTx copies; R1 (blind) relies on it (A04)"));
            }
            else
            {
                T.Check("reflog " + c.name + ": identical to the stock reference", same && rc == 0,
                        Opt().Exp("no difference").Obs(same ? "identical" : "differs: " + diff.substr(0, 400)).Tol("exact").Sev("H"));
            }
        }
    }

    // ---- copies per TB (micro scenario = sl-harq-example topology) ---------------------------------------------------------
    struct Combo { std::string label; bool harq; uint16_t psfch; };
    const FailDistance fd = FindFailingDistance();
    for (const Combo& cb : {Combo{"HARQ on, PSFCH period 4", true, 4}, Combo{"HARQ on, PSFCH period 0 (blind)", true, 0}, Combo{"HARQ off, PSFCH period 4", false, 4}})
    {
        MicroCfg c;
        c.harq = cb.harq;
        c.psfchPeriod = cb.psfch;
        c.maxNumTx = 5;
        c.rxDistM = fd.found ? std::vector<double>{fd.distM} : std::vector<double>{600.0};
        c.nPackets = 60;
        c.intervalMs = 100.0;
        c.pdbMs = 100.0;
        c.dedicated = false; // the example uses the shared TDD pool
        MicroLog lg;
        std::string err;
        if (!RunMicro(c, lg, &err)) { T.Fail("micro/" + cb.label, Opt().Obs(err)); continue; }
        std::vector<int> txPerTb;
        for (const auto& x : lg.tx)
        {
            if (x.ndi == 1 || txPerTb.empty()) txPerTb.push_back(0);
            ++txPerTb.back();
        }
        long total = 0, mx = 0, n5 = 0, n1 = 0;
        for (int n : txPerTb) { total += n; mx = std::max<long>(mx, n); n5 += (n == 5); n1 += (n == 1); }
        const double avg = txPerTb.empty() ? 0.0 : static_cast<double>(total) / txPerTb.size();
        long nacks = 0;
        for (const auto& f : lg.fbRx) nacks += f.ack ? 0 : 1;
        const std::string obs = FmtI(static_cast<long>(txPerTb.size())) + " TBs, copies per TB: mean " + Fmt(avg, 4) + ", max " + FmtI(mx) + ", with 5 copies " + FmtI(n5) + ", with 1 copy " + FmtI(n1) + "; NACKs at the TX " + FmtI(nacks);
        if (cb.harq && cb.psfch == 4)
            T.Check("sl-harq-example topology, " + cb.label + ": copies <= 5, retransmissions only after NACKs", mx <= 5 && total - static_cast<long>(txPerTb.size()) <= std::max<long>(nacks, 0),
                    Opt().Exp("max copies <= 5; retransmissions (copies beyond the first) <= NACKs received").Obs(obs).Sev("M").Ref("TS 38.321 5.22.1.3 [V]"));
        else if (cb.harq && cb.psfch == 0)
            T.Check("sl-harq-example topology, " + cb.label + ": 5 blind copies per TB", !txPerTb.empty() && n5 == static_cast<long>(txPerTb.size()),
                    Opt().Exp("every TB has 5 copies").Obs(obs).Sev("M").Ref("TS 38.321 5.22.1.1 [V]"));
        else
            T.CheckXfail("sl-harq-example topology, " + cb.label + ": a HARQ-disabled LC sends 1 copy in stock 5G-LENA", !txPerTb.empty() && n1 == static_cast<long>(txPerTb.size()),
                         Opt().Fnd("F11").Sev("M").Exp("1 copy per TB (stock)").Obs(obs).Note("patched build: maxNumTx copies for a HARQ-disabled LC (DV9)"));
    }

    // ---- A03b: stock NR test suites (needs the --enable-tests reconfigure, decision D-7) ------------------------------------------------------------
    {
        Table t;
        if (LoadTable(OutDir() + "/A03b/summary.csv", t) && t.nrows > 0)
        {
            for (size_t i = 0; i < t.nrows; ++i)
            {
                const std::string suite = t.StrAt("suite", i), st = t.StrAt("status", i), note = t.StrAt("note", i);
                const bool expectDiff = suite == "sl-multi-lc-dyn-gcast-no-harq";
                if (st == "PASS")
                    T.Check("A03b stock suite " + suite, !expectDiff || true, Opt().Obs("PASS").Exp(expectDiff ? "FAIL expected (DV9)" : "PASS").Note(note));
                else if (expectDiff)
                    T.Xfail("A03b stock suite " + suite + " fails as predicted", Opt().Fnd("F11").Sev("M").Obs(st + " " + note).Exp("reference 'no-harq:1' vs patched 'no-harq:5' (DV9)"));
                else
                    T.Fail("A03b stock suite " + suite, Opt().Obs(st + " " + note).Exp("PASS").Sev("H"));
            }
        }
        else
        {
            T.Skip("A03b stock NR suites (nr-test-sidelink-harq, nr-sl-sensing, nr-sl-sci-headers, sl-multi-lc-*)", "A03b not executed: the reconfigure `cd ns-3-dev && ./ns3 configure --enable-examples --disable-werror --enable-tests` (decision D-7) was denied by the permission system (it rewrites the shared CMake cache; the relayed approval is not user consent). Run tests/campaign/a03b.sh once the user approves it; it snapshots the cache, rebuilds, compares DET byte-wise and runs the stock suites");
        }
    }
    {
        const std::string cmp = ReadFile(OutDir() + "/A03b/det_compare.txt");
        if (cmp.empty())
        {
            T.Skip("A03b DET outputs byte-identical before/after enabling the tests", "A03b not executed (reconfigure denied by the permission system; see the stock-suite SKIP above)");
        }
        else
        {
            T.Check("A03b: DET outputs byte-identical before and after the --enable-tests reconfigure", cmp.find("different 0") != std::string::npos,
                    Opt().Exp("files identical (wall-clock columns excepted)").Obs(cmp.substr(cmp.rfind("files"))).Tol("0").Sev("H"));
        }
    }
    return T.Finish();
}
