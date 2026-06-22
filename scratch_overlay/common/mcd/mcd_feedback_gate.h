#pragma once
// common/mcd/mcd_feedback_gate.h
//
// MCD (Maximum Communication Distance) feedback gate for NR-V2X sidelink.
//
// Problem: in a groupcast with Option-2 HARQ (CastType::Groupcast), every UE
// that has an SL-DRB for the destination L2 ID will send PSFCH ACK/NACK
// regardless of distance. The 3GPP spec gates feedback sending on whether the
// receiver is within MCD of the transmitter of that TB (not the original
// leader). This module implements that gate WITHOUT editing contrib/nr.
//
// Mechanism:
//   1. RntiMobilityRegistry — callers register (RNTI, MobilityModel) after
//      the NR stack is built. This lets the gate shim resolve the TX node's
//      mobility from the SlHarqInfo::m_txRnti field.
//   2. InstallMcdFeedbackGate() — after NrSlHelper wires its callback
//      (SetPhySlHarqFeedbackCallback → NrUePhy::EnqueueSlHarqFeedback), this
//      function RE-BINDS that callback on every UE spectrum PHY to a shim
//      that:
//        a. looks up the TX mobility from m_txRnti,
//        b. computes distance between RX and TX,
//        c. if distance > mcdMeters: drops the feedback silently,
//        d. otherwise: forwards to the original EnqueueSlHarqFeedback.
//   3. The registry is populated per-BWP (key = (rnti, bwpId)); the gate
//      uses the BWP of the feedback message (m_bwpIndex) for correct lookup
//      in multi-BWP scenarios. In single-BWP scenarios (bwpId 0) the key
//      degenerates to just rnti.
//
// No edits to ns-3-dev/contrib/nr are required.

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
// Install the MCD feedback gate on all UE devices.
//
// Must be called AFTER:
//   ctx.sl->PrepareUeForSidelink(...)
//   ctx.sl->InstallNrSlPreConfiguration(...)
//   -- i.e., after NrSlHelper has already set SetPhySlHarqFeedbackCallback.
//
// For each UE in ueNetDev, for each BWP (bwpId 0..numBwps-1):
//   - Retrieves the current callback (which is NrUePhy::EnqueueSlHarqFeedback).
//   - Re-binds the spectrum PHY callback to a shim that gates on distance.
//
// mcdMeters: maximum distance (m) from the TB transmitter within which a
//            receiver may send PSFCH feedback. Set to +inf (e.g. 1e9) to
//            disable gating (no-op pass-through).
// ---------------------------------------------------------------------------
void InstallMcdFeedbackGate(const NetDeviceContainer& ueNetDev,
                            double mcdMeters);

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
