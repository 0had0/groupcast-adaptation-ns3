// SPDX-License-Identifier: GPL-2.0-only
#pragma once
// NrSlDeterministicScheduler
// ==========================
// A collision-free (deterministic) NR sidelink Mode-2 scheduler, implemented
// entirely in scratch (no ns-3-dev edits). It is a drop-in replacement for
// NrSlUeMacSchedulerFixedMcs that *constrains* the resource that the parent may
// pick, so that transmitters assigned to disjoint partitions can never select
// overlapping subchannel/slot resources. This removes the sensing-based SPS
// (SB-SPS) collisions (the p_col -> 0 deterministic bound).
//
// It does NOT reimplement TBS sizing or the grant machinery: it filters the
// candidate-resource list handed to it and then delegates to the parent, so all
// MCS/TBS behaviour is identical to the fixed-MCS scheduler.
//
// Flexibility (reusable in other simulations):
//   * Frequency division (FDM): SetSubchannelLane(start,len) restricts this UE
//     to subchannels [start, start+len).
//   * Time division (TDM): SetSlotPartition(modulus,phase) restricts this UE to
//     SL slots whose normalized index n satisfies (n % modulus == phase).
//   * The two can be combined (FDM+TDM) or left unset (then the scheduler is a
//     transparent pass-through == fixed-MCS behaviour, so it is always safe to
//     install).
//   * Per-destination fixed MCS via SetDstMcsOverride(dstL2Id,mcs), so it can be
//     used for split-MCS groupcast just like NrSlAdaptiveMcsScheduler.
//   * Strict mode (default): if no candidate falls in this UE's partition the
//     slot is skipped (true determinism). Non-strict falls back to the full
//     candidate set (graceful degradation).
//   * Static helper Lane(totalSubch,numLanes,laneIdx) to split a pool evenly.
//
// All knobs are exposed both as setters (for per-instance wiring) and as TypeId
// attributes (for helper-based SetUeSlSchedulerAttribute configuration).

#include "ns3/nr-sl-phy-mac-common.h"
#include "ns3/nr-sl-ue-mac-scheduler-dst-info.h"
#include "ns3/nr-sl-ue-mac-scheduler-fixed-mcs.h"
#include "ns3/object.h"

#include <list>
#include <map>
#include <memory>
#include <set>
#include <utility>

namespace ns3
{

class NrSlDeterministicScheduler : public NrSlUeMacSchedulerFixedMcs
{
  public:
    static TypeId GetTypeId();

    NrSlDeterministicScheduler() = default;
    ~NrSlDeterministicScheduler() override = default;

    /// Restrict grants to subchannels [start, start+length). length==0 => no FDM limit.
    void SetSubchannelLane(uint8_t start, uint8_t length);

    /// Restrict grants to SL slots with (Normalize() % modulus == phase). modulus==0 => no TDM limit.
    void SetSlotPartition(uint32_t modulus, uint32_t phase);

    /// If true (default), skip the slot when no candidate fits the partition.
    void SetStrict(bool strict);

    /// Pin a fixed MCS for a given destination L2 id (split-MCS groupcast).
    void SetDstMcsOverride(uint32_t dstL2Id, uint8_t mcs);

    /// Convenience: even split of a pool of totalSubch into numLanes; returns
    /// {start,length} for laneIdx (clamped). Lets callers assign disjoint lanes.
    static std::pair<uint8_t, uint8_t> Lane(uint8_t totalSubch,
                                            uint8_t numLanes,
                                            uint8_t laneIdx);

    /// Counters for verification (e.g. how often the partition skipped a slot).
    uint64_t GetAllocAttempts() const { return m_attempts; }
    uint64_t GetAllocSkipped() const { return m_skipped; }

  protected:
    bool DoNrSlAllocation(const std::list<SlResourceInfo>& candResources,
                          const std::shared_ptr<NrSlUeMacSchedulerDstInfo>& dstInfo,
                          std::set<SlGrantResource>& slotAllocList,
                          const AllocationInfo& allocationInfo) override;

  private:
    bool InLane(const SlResourceInfo& r) const;
    bool InSlotPhase(const SlResourceInfo& r) const;

    uint8_t m_laneStart{0};
    uint8_t m_laneLength{0}; // 0 => no FDM restriction
    uint32_t m_slotModulus{0}; // 0 => no TDM restriction
    uint32_t m_slotPhase{0};
    bool m_strict{true};

    std::map<uint32_t, uint8_t> m_dstMcsOverride;

    uint64_t m_attempts{0};
    uint64_t m_skipped{0};
};

} // namespace ns3
