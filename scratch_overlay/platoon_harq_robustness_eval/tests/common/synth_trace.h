// synth_trace.h -- static synthetic platoon traces in the LoadTrace format (C++ twin of
// campaign/make_synth_traces.py; the two must write byte-identical files, A05 compares them).
#pragma once

#include <string>
#include <utility>
#include <vector>

namespace ptest
{

/// geometry names: SYN_NEAR, SYN_MARGINAL, SYN_DTX, SYN_MCR, SYN_SPS, SYN_DIST_<spacing>
std::vector<std::pair<double, double>> SynthMembers(const std::string& geom);
/// full file text (meta lines, header, rows) of the trace
std::string RenderSynthTrace(const std::string& geom, double tEnd = 20.0, double kpiStart = 3.0);
std::vector<std::string> SynthGeometries();

} // namespace ptest
