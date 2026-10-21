// runsets.h -- enumerate the runs of a run-set from tests/out/<runset>/manifest.csv (written by
// campaign/run_matrix.py; columns runset,arm,scenario,ivd,bg,traffic,seed,leader_sps,tag,out_prefix,exit,
// wall_s,max_rss_mb,cmd).
#pragma once

#include <string>
#include <vector>

namespace ptest
{

struct RunRef
{
    std::string runset, arm, scenario, ivd, bg, traffic, tag, prefix, cmd;
    int seed{0};
    bool leaderSps{true};
    int exitCode{0};
    double wallS{0}, maxRssMb{0};

    /// "R1".."R6" (arm prefix) or "" for DET variants
    std::string Retx() const;
    /// "H1" | "H2" | "H5" | "" (leader-only arms)
    std::string Sel() const;
    bool IsHelperArm() const { return !Sel().empty(); }
    /// "blind" | "opt1" | "opt2"
    std::string HarqMode() const;
    /// the leader-only counterpart (R4->R1, R5->R2, R6->R3)
    std::string Counterpart() const;
    /// cell key without arm/seed: "<scenario>/ivd/bg/traffic[/sps0]"
    std::string CellKey() const;
};

/// Runs of a run-set (empty if no manifest). Only complete runs (exit 0) are listed.
/// periodic2fx run without --t2Ms: carries the F32 misfit (TEST_PLAN 0.4)
inline bool P2fxNoT2(const RunRef& r) { return r.traffic == "periodic2fx" && r.cmd.find("--t2Ms") == std::string::npos; }

/// manifest of a run-set; with outDir (e.g. OutDirV1()) the output prefixes are rebuilt as outDir/<runset>/<arm>/<tag> (archived trees keep stale absolute paths)
std::vector<RunRef> LoadManifest(const std::string& runset, const std::string& outDir = "");
/// Runs of several run-sets
std::vector<RunRef> LoadManifests(const std::vector<std::string>& runsets);

std::vector<RunRef> Select(const std::vector<RunRef>& v, const std::string& arm = "",
                           const std::string& traffic = "", const std::string& bg = "",
                           const std::string& scenario = "", const std::string& ivd = "");

/// all arms in the paper order of the 10-arm study
const std::vector<std::string>& StudyArms();
double PdbSeconds(const std::string& traffic);
double RriMsOfTraffic(const std::string& traffic); //!< SPS reservation period of the leader flow (ms)

} // namespace ptest
