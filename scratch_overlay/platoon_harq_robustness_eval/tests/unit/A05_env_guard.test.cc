// A05_env_guard: the debug hooks MAGIC_HELPER_ID / NUM_MEMBERS must be unset in every run (F03); the hook exists in
// nr-spectrum-phy.cc and is live when set (proved once on the MAGIC run-set); every manifest recorded the unset env.
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/synth_trace.h"

#include <dirent.h>

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>

using namespace ptest;

static std::vector<std::string>
GrepLines(const std::string& file, const std::string& needle)
{
    std::vector<std::string> out;
    std::ifstream f(file);
    std::string line;
    int n = 0;
    while (std::getline(f, line))
    {
        ++n;
        if (line.find(needle) != std::string::npos)
        {
            out.push_back(std::to_string(n));
        }
    }
    return out;
}

static std::string
Join(const std::vector<std::string>& v)
{
    std::string s;
    for (size_t i = 0; i < v.size(); ++i)
    {
        s += (i ? "," : "") + v[i];
    }
    return s;
}

static std::string
ReadAll(const std::string& p)
{
    std::ifstream f(p, std::ios::binary);
    std::ostringstream os;
    os << f.rdbuf();
    return os.str();
}

int
main()
{
    TestReport T("A05", "env_guard", "A");
    T.Summary("MAGIC_HELPER_ID / NUM_MEMBERS are unset in the test process and in every recorded run; the "
              "MAGIC_HELPER_ID hook exists in nr-spectrum-phy.cc and is live when set (F03).");
    T.AddRef("TEST_PLAN F03");

    // 1. environment of this process (run_tests.sh unsets both)
    {
        const char* a = std::getenv("MAGIC_HELPER_ID");
        const char* b = std::getenv("NUM_MEMBERS");
        T.Check("env/MAGIC_HELPER_ID unset in this process", a == nullptr,
                Opt().Exp("unset").Obs(a ? a : "unset").Sev("H").Fnd("F03"));
        T.Check("env/NUM_MEMBERS unset in this process", b == nullptr,
                Opt().Exp("unset").Obs(b ? b : "unset").Sev("H"));
    }

    // 2. the hook exists in the patched NR module
    {
        const std::string f = RepoRoot() + "/ns-3-dev/contrib/nr/model/nr-spectrum-phy.cc";
        auto lines = GrepLines(f, "MAGIC_HELPER_ID");
        if (!FileExists(f))
        {
            T.Skip("hook/MAGIC_HELPER_ID in nr-spectrum-phy.cc", "file not found: " + f);
        }
        else if (lines.empty())
        {
            T.Pass("hook/MAGIC_HELPER_ID absent from nr-spectrum-phy.cc", Opt().Exp("absent or present (information)").Obs("absent"));
        }
        else
        {
            T.Xfail("hook/MAGIC_HELPER_ID present in nr-spectrum-phy.cc",
                    Opt().Fnd("F03").Sev("H").Exp("no debug env hook in the NR PHY")
                        .Obs("lines " + Join(lines))
                        .Note("PSCCH/PSSCH decode success is forced for node id (MAGIC_HELPER_ID-1) when the variable is set; "
                              "std::stoi throws on a non-numeric value. The hook is inactive in this run because the launch "
                              "scripts unset the variable (checks above and below)."));
        }
        auto nm = GrepLines(RepoRoot() + "/ns-3-dev/contrib/nr/model/nr-sl-ue-mac-harq.cc", "NUM_MEMBERS");
        T.Info("hook/NUM_MEMBERS read in nr-sl-ue-mac-harq.cc",
               Opt().Obs(nm.empty() ? "not read there" : "lines " + Join(nm))
                   .Note("main() sets NUM_MEMBERS itself via setenv (platoon_harq_robustness_eval.cc); the launch script unsets it "
                         "before exec, the simulator sets it for the 'all members must ACK' MAC variant"));
    }

    // 3. every manifest recorded the unset env (MAGIC probe excepted)
    {
        int nManifests = 0;
        long nRuns = 0, bad = 0;
        std::string badEx;
        DIR* d = opendir(OutDir().c_str());
        if (d)
        {
            while (dirent* e = readdir(d))
            {
                std::string nm = e->d_name;
                if (nm == "." || nm == ".." || nm == "traces" || nm == "logs")
                {
                    continue;
                }
                auto runs = LoadManifest(nm);
                if (runs.empty())
                {
                    continue;
                }
                ++nManifests;
                for (const auto& r : runs)
                {
                    ++nRuns;
                    const bool magicProbe = (nm == "MAGIC");
                    const bool unsetBoth = r.cmd.find("-u MAGIC_HELPER_ID") != std::string::npos &&
                                           r.cmd.find("-u NUM_MEMBERS") != std::string::npos;
                    const bool magicSet = r.cmd.find("MAGIC_HELPER_ID=") != std::string::npos;
                    const bool ok = magicProbe ? (magicSet && r.cmd.find("-u NUM_MEMBERS") != std::string::npos)
                                               : (unsetBoth && !magicSet);
                    if (!ok)
                    {
                        ++bad;
                        if (badEx.empty())
                        {
                            badEx = nm + "/" + r.arm + "/" + r.tag;
                        }
                    }
                }
            }
            closedir(d);
        }
        T.AddNRuns(nRuns);
        if (nManifests == 0)
        {
            T.Skip("manifests/env recorded unset", "no run-set manifest found yet (synth stage not run)");
        }
        else
        {
            T.Check("manifests/every run launched with MAGIC_HELPER_ID and NUM_MEMBERS unset (MAGIC probe excepted)",
                    bad == 0,
                    Opt().Exp("0 runs with the hook env").Obs(FmtI(bad) + " of " + FmtI(nRuns) + " runs in " +
                                                              FmtI(nManifests) + " manifests" +
                                                              (bad ? "; first: " + badEx : ""))
                        .Sev("H").Fnd("F03").Tol("0"));
        }
    }

    // 4. the hook is live (MAGIC probe vs the same configuration with the env unset)
    {
        auto magic = LoadManifest("MAGIC");
        auto base = Select(LoadManifest("SYN_MARGINAL"), "R6_S1_H2", "periodic1tb");
        const RunRef* b = nullptr;
        for (const auto& r : base)
        {
            if (r.seed == 1 && r.leaderSps)
            {
                b = &r;
            }
        }
        if (magic.empty() || !b)
        {
            T.Skip("hook/live probe", "MAGIC run or its SYN_MARGINAL R6_S1_H2 periodic1tb seed-1 baseline missing");
        }
        else
        {
            T.AddRunset("MAGIC");
            T.AddRunset("SYN_MARGINAL");
            Run rm, rb;
            if (!rm.Load(magic[0]) || !rb.Load(*b))
            {
                T.Skip("hook/live probe", "run outputs unreadable");
            }
            else
            {
                const std::string ms = ReadAll(ResolveCsv(magic[0].prefix + "_member.csv"));
                const std::string bs = ReadAll(ResolveCsv(b->prefix + "_member.csv"));
                const bool differ = ms != bs;
                const double m3m = rm.MemberCol("prr", 3), m3b = rb.MemberCol("prr", 3);
                const double m4m = rm.MemberCol("prr", 4), m4b = rb.MemberCol("prr", 4);
                T.Check("hook/live: MAGIC_HELPER_ID=4 changes the outputs vs the unset run", differ,
                        Opt().Exp("outputs differ (the hook forces decode success of node id 3 = member 3)")
                            .Obs(std::string(differ ? "differ" : "identical") + "; member-3 PRR " + Fmt(m3b) + " -> " +
                                 Fmt(m3m) + ", member-4 PRR " + Fmt(m4b) + " -> " + Fmt(m4m))
                            .Sev("H").Fnd("F03")
                            .Note("probe run on SYN_MARGINAL (not SYN_NEAR as planned): on the lossless SYN_NEAR ring nothing "
                                  "could differ"));
                T.Info("hook/live: member 3 decode statuses",
                       Opt().Obs("member 3 att_corrupt+att_missed with hook: " +
                                 Fmt(rm.MemberCol("att_corrupt", 3) + rm.MemberCol("att_missed", 3), 8) + ", without: " +
                                 Fmt(rb.MemberCol("att_corrupt", 3) + rb.MemberCol("att_missed", 3), 8)));
            }
        }
    }

    // 5. C++ synth_trace == Python make_synth_traces
    {
        long cmp = 0, same = 0;
        std::string firstDiff;
        for (const auto& g : SynthGeometries())
        {
            const std::string p = OutDir() + "/traces/" + g + ".csv";
            if (!FileExists(p))
            {
                continue;
            }
            ++cmp;
            if (ReadAll(p) == RenderSynthTrace(g))
            {
                ++same;
            }
            else if (firstDiff.empty())
            {
                firstDiff = g;
            }
        }
        if (cmp == 0)
        {
            T.Skip("synth/C++ twin equals Python trace writer", "no synthetic trace written yet");
        }
        else
        {
            T.Check("synth/C++ twin equals Python trace writer", cmp == same,
                    Opt().Exp(FmtI(cmp) + " identical files").Obs(FmtI(same) + " identical" + (firstDiff.empty() ? "" : "; differs: " + firstDiff)));
        }
    }

    return T.Finish();
}
