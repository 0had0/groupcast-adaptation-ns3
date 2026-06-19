#pragma once
// sl_stack_bler.h
//
// NR + NR-Sidelink (Mode 2, autonomous SB-SPS) stack builder specialised for a
// BLER-vs-SNR study. Derived from
//   scratch_overlay/relay_groupcast_eval/nr_sl_stack.cc
// but:
//   * fixed-MCS only (no adaptive / deterministic schedulers),
//   * deterministic channel-condition control via SlChannelCond:
//       Los   -> AlwaysLosChannelConditionModel on every BWP
//       Nlos  -> NeverLosChannelConditionModel  on every BWP
//       Nlosv -> geometric NLOSv wrapper (ApplyGeometricNlosv); the 3GPP V2V
//                model adds one log-normal blockage term per TR 37.885 §6.4.1
//                regardless of how many blockers are present (correct per spec),
//   * a configurable (realtime/per-slot) channel update period.
//
// We reuse SlStackParams / SlStackCtx / GeomChannelCfg from the relay header so
// that the reused groupcast_flows.cc keeps working unchanged. We do NOT compile
// the original nr_sl_stack.cc (so there is no duplicate BuildSlStack symbol).

#include "common/stack/nr_sl_stack.h"

namespace ns3
{

enum class SlChannelCond
{
    Los,
    Nlos,
    Nlosv
};

// Build the stack on `nodes` (exactly the V2X UEs — blocker-only nodes are NOT
// passed here; they reach the channel through geom.blockerNodes).
//   cond            : LOS / NLOS / NLOSv forcing.
//   channelUpdateMs : ThreeGppChannelModel + CCM UpdatePeriod in ms (must be >0;
//                     pass the NR slot duration 1/2^mu for "realtime").
SlStackCtx BuildSlStackBler(const SlStackParams& p,
                            const GeomChannelCfg& geom,
                            SlChannelCond cond,
                            double channelUpdateMs,
                            const NodeContainer& nodes);

} // namespace ns3
