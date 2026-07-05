#pragma once
// common/mcd/mcr_feedback_gate.h
//
// CastType-aware PSFCH feedback gate for NR-V2X sidelink.
//
// Per 3GPP TS 38.321 §5.4.4.1.1 and TS 38.213 §16.5, a sidelink UE's HARQ
// feedback generation in PSFCH is gated by castType:
//
//   * Broadcast (castTypeIndicator = 0):
//       Spec: NO PSFCH feedback of any kind. (Broadcast is fire-and-forget;
//       the receiver either decoded or it didn't, and the sender never
//       retransmits because there's no per-RX ACK/NACK target list.)
//
//   * HARQ Option 1 (castTypeIndicator = GroupcastOnlyNack = 3):
//       Spec: Receiver sends NACK only when it failed to decode AND it is
//       within MCR of the transmitter. Out-of-range receivers are silent.
//       No PSFCH transmission on success (silent-on-success).
//       Sender interprets silence as NACK (retransmit).
//
//   * HARQ Option 2 (castTypeIndicator = Groupcast = 1):
//       Spec: Receiver sends ACK or NACK regardless of distance. The spec
//       gates this by Zone ID membership (sl-ZoneConfig in RRC) —
//       out-of-zone receivers are silent regardless of distance. There is NO
//       distance-based filter for Option 2.
//
//   * Unicast (castTypeIndicator = Unicast = 2):
//       Spec: ACK or NACK regardless of distance (no gate).
//
// contrib/nr does NOT implement any of these gates at the PHY level — it
// fires PSFCH feedback unconditionally for any decoded TB with
// m_isHarqEnabled=true, regardless of castType or distance.
//
// This module implements all four castType behaviors at the receiver-side
// PSFCH callback without editing contrib/nr:
//
//   1. RntiMobilityRegistry — callers register (RNTI, MobilityModel) after
//      the NR stack is built. Lets the gate shim resolve the TX node's
//      mobility from the SlHarqInfo::m_txRnti field.
//   2. DstL2IdCastTypeRegistry — callers register (dstL2Id, castType) at
//      flow install time. Lets the gate shim determine the castType of the
//      originating TB from the SlHarqInfo::m_dstL2Id field.
//   3. InstallMcrFeedbackGate() — after NrSlHelper wires its callback
//      (SetPhySlHarqFeedbackCallback → NrUePhy::EnqueueSlHarqFeedback),
//      this function RE-BINDS that callback on every UE spectrum PHY to a
//      shim that:
//        a. looks up the castType from m_dstL2Id,
//        b. if castType is Broadcast: drops the feedback silently,
//        c. if castType is Groupcast (Option-2): forwards the feedback
//           unchanged (no distance gate — spec uses Zone-ID which we don't
//           model; in single-dstL2Id setups all UEs are members so no
//           filter is spec-faithful),
//        d. if castType is Unicast: forwards the feedback unchanged,
//        e. if castType is GroupcastOnlyNack (Option-1): applies the MCR
//           distance gate — drops feedback if distance > mcrMeters, else
//           forwards to the original EnqueueSlHarqFeedback.
//
// IMPORTANT — this module is castType-aware but the install-time flag
// `mcrMeters` is only consulted for castType == GroupcastOnlyNack. For
// Broadcast, all feedback is dropped unconditionally regardless of mcrMeters.
// For Groupcast (Option-2) and Unicast, mcrMeters is ignored.
//
// SlStackParams::harqOption controls whether BuildSlStack installs this
// gate (harqOption == 1, GroupcastOnlyNack) or skips it (harqOption == 2,
// Groupcast — spec uses Zone-ID, not distance, so no gate is installed).
//
// PSFCH multiplexing note: contrib/nr does not implement the actual 3GPP
// PSFCH freq×time×code multiplexing — each RX UE's feedback is delivered
// to the sender's MAC as a discrete callback carrying nodeId. This is
// acceptable for small-N sweeps (≤20 RX per TB); for larger N, the spec-
// defined PSFCH capacity limit (typically 4-8 sequences per PSFCH RB) is
// not modeled.

#include "ns3/mobility-model.h"
#include "ns3/net-device-container.h"
#include "ns3/nr-phy-mac-common.h"
#include "ns3/nr-ue-phy.h"
#include "ns3/ptr.h"

#include <cstdint>
#include <map>
#include <utility>

namespace ns3
{

// ---------------------------------------------------------------------------
// RNTI-to-mobility registry.
// Key: (rnti, bwpId). In the common single-BWP case bwpId=0.
// ---------------------------------------------------------------------------
class RntiMobilityRegistry
{
  public:
    using Key = std::pair<uint16_t, uint8_t>; // (rnti, bwpId)

    // Register a TX node so gate shims can resolve its mobility by RNTI.
    void Register(uint16_t rnti, uint8_t bwpId, Ptr<MobilityModel> mob);

    // Look up mobility; returns nullptr if not found.
    Ptr<MobilityModel> Lookup(uint16_t rnti, uint8_t bwpId) const;

    // Singleton accessor — one shared registry per simulation instance.
    static RntiMobilityRegistry& Instance();

  private:
    std::map<Key, Ptr<MobilityModel>> m_map;
};

// ---------------------------------------------------------------------------
// dstL2Id → castType registry.
//
// castType values match NrSlSciF2aHeader::CastTypeIndicator_t
// (ns-3-dev/contrib/nr/model/nr-sl-sci-f2a-header.h):
//   0 = Broadcast
//   1 = Groupcast (Option-2, ACK/NACK)
//   2 = Unicast
//   3 = GroupcastOnlyNack (Option-1, NACK-only)
//
// Callers (InstallGroupcastFlows and the broadcast setup paths) must call
// RegisterDstL2IdCastType() once per dstL2Id before Simulator::Run() so the
// gate shim can look up the castType for incoming feedback.
// ---------------------------------------------------------------------------
class DstL2IdCastTypeRegistry
{
  public:
    // Register a dstL2Id's castType. Idempotent — last write wins.
    void Register(uint32_t dstL2Id, uint8_t castType);

    // Look up castType for a dstL2Id. Returns the default (Groupcast = 1)
    // if not registered — this matches the most common single-dstL2Id
    // groupcast setup, which is what existing callers expect.
    uint8_t Lookup(uint32_t dstL2Id) const;

    // Singleton accessor.
    static DstL2IdCastTypeRegistry& Instance();

  private:
    std::map<uint32_t, uint8_t> m_map;
};

// ---------------------------------------------------------------------------
// Convenience: register a dstL2Id's castType for the gate shim to look up.
// Idempotent — last write wins. Safe to call multiple times for the same
// dstL2Id (e.g., across multiple flow installs).
// ---------------------------------------------------------------------------
void RegisterDstL2IdCastType(uint32_t dstL2Id, uint8_t castType);

// ---------------------------------------------------------------------------
// Install the castType-aware feedback gate on all UE devices.
//
// Must be called AFTER:
//   ctx.sl->PrepareUeForSidelink(...)
//   ctx.sl->InstallNrSlPreConfiguration(...)
//   -- i.e., after NrSlHelper has already set SetPhySlHarqFeedbackCallback.
//
// For each UE in ueNetDev, for each BWP (bwpId 0..numBwps-1):
//   - Retrieves the current callback (which is NrUePhy::EnqueueSlHarqFeedback).
//   - Re-binds the spectrum PHY callback to a shim that gates on castType
//     and (for GroupcastOnlyNack) on distance.
//
// mcrMeters: minimum communication range (m). For castType=GroupcastOnlyNack,
//            a receiver farther than this from the TB transmitter has its
//            feedback dropped. For castType=Broadcast, all feedback is
//            dropped unconditionally (mcrMeters is irrelevant). For
//            castType=Groupcast or Unicast, mcrMeters is ignored (no gate).
//            Set to +inf (e.g. 1e9) to disable the MCR gate even for
//            GroupcastOnlyNack (no-op pass-through).
//
// SlStackParams::harqOption controls whether BuildSlStack installs this
// gate (harqOption == 1) or skips it (harqOption == 2). When harqOption
// is 2, this function should not be called at all; the per-castType rules
// (Broadcast = no feedback, Groupcast = no gate) are applied directly by
// the flow paths instead.
// ---------------------------------------------------------------------------
void InstallMcrFeedbackGate(const NetDeviceContainer& ueNetDev,
                            double mcrMeters);

// ---------------------------------------------------------------------------
// Convenience: populate the RntiMobilityRegistry from a built NR device
// container. Call once after RNTI assignment.
//
// BLOCKING version — expects all UEs to have non-zero RNTIs already.
// For use inside a Simulator event after the NR RRC has run.
// ---------------------------------------------------------------------------
void RegisterRntiMobility(const NetDeviceContainer& ueNetDev);

// ---------------------------------------------------------------------------
// Polling version — schedules itself via Simulator::Schedule(MilliSeconds(1))
// until all UEs have non-zero RNTIs, then calls RegisterRntiMobility().
//
// Use this when called before the NR RRC has assigned RNTIs (e.g., from
// BuildSlStack, before Simulator::Run starts).  NR RNTI assignment happens
// via asynchronous events (StartContentionResolution etc.) that only run
// during Simulator::Run(), so a synchronous call finds all RNTIs = 0.
// ---------------------------------------------------------------------------
void RegisterRntiMobilityPoll(const NetDeviceContainer& ueNetDev);

} // namespace ns3