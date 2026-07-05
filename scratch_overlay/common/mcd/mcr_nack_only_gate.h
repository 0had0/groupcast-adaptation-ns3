#pragma once
// common/mcd/mcr_nack_only_gate.h
//
// HARQ Option-1 (NACK-only) feedback gate for NR-V2X sidelink groupcast.
//
// Problem: in 3GPP TS 38.321, HARQ feedback for sidelink groupcast has TWO
// operating modes:
//   - Option 1 (NACK-only): receivers send NACK on the PSFCH only when the TB
//     decode FAILED. Successful decodes are SILENT (no ACK transmitted).
//   - Option 2 (ACK/NACK): receivers always send an explicit ACK or NACK.
//
// The contrib/nr model implements Option 2: in nr-spectrum-phy.cc the
// `EnqueueSlHarqFeedback` callback is invoked with `m_harqStatus=ACK` for
// successful decodes. Option 1 is not directly wired in the scheduler/MAC path.
//
// We emulate Option 1 with a NO-EDIT shim layered on top of the existing
// `SetPhySlHarqFeedbackCallback` machinery — the same pattern used by
// McrFeedbackGate (see common/mcd/mcr_feedback_gate.h):
//
//   1. RntiMobilityRegistry — reused unchanged (registered by BuildSlStack).
//   2. InstallMcrNackOnlyGate() — re-binds the spectrum-phy PSFCH callback on
//      every UE device to a shim that:
//        a. looks up the TX mobility from m_txRnti,
//        b. computes distance between RX and TX,
//        c. if distance > mcrMeters: drops the feedback entirely (out-of-MCR
//           receivers are silent in both Options),
//        d. if distance <= mcrMeters AND feedback is NACK: forwards to the
//           original NrUePhy::EnqueueSlHarqFeedback (Option-1 NACK semantics),
//        e. if distance <= mcrMeters AND feedback is ACK: drops the feedback
//           (Option-1 silent-on-success semantics).
//
// Effects observable in KPIs:
//   * slHarqFeedback table contains ONLY Nack rows for in-range receivers.
//   * Resource allocation / re-TX behavior is unchanged (the leader still sees
//     NACKs as NACKs and proceeds identically to Option 2).
//   * PSSCH utilization and collision patterns are identical to Option 2;
//     PSFCH load is roughly halved (no ACKs transmitted or scheduled).
//
// No edits to ns-3-dev/contrib/nr are required.

#include "common/mcd/mcr_feedback_gate.h"

#include "ns3/net-device-container.h"

#include <cstdint>

namespace ns3
{

// ---------------------------------------------------------------------------
// Install the MCR NACK-only feedback gate on all UE devices.
//
// Must be called AFTER:
//   ctx.sl->PrepareUeForSidelink(...)
//   ctx.sl->InstallNrSlPreConfiguration(...)
//   InstallMcrFeedbackGate() (if used) — OR instead of it
// so that NrSlHelper has already set the original SetPhySlHarqFeedbackCallback
// and the RntiMobilityRegistry is populated.
//
// For each UE in ueNetDev, for each BWP:
//   - Retrieves the current callback (which may be McrGateShim::OnFeedback
//     or NrUePhy::EnqueueSlHarqFeedback).
//   - Re-binds to a shim that emulates Option-1 ACK-drop semantics inside
//     the MCR range.
//
// mcrMeters: maximum distance (m) within which receivers may send PSFCH.
//            Set to +inf (e.g. 1e9) to disable the MCR-range gate (all RX
//            within range, but ACKs still dropped if dropAcks=true).
// dropAcks:  when true, drop ACK feedback inside MCR (Option 1 semantics).
//            when false, behave as pass-through (a useful debug knob).
// ---------------------------------------------------------------------------
void InstallMcrNackOnlyGate(const NetDeviceContainer& ueNetDev,
                            double mcrMeters,
                            bool dropAcks = true);

} // namespace ns3