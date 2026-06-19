// SPDX-License-Identifier: GPL-2.0-only
#pragma once
// NrSlAdaptiveMcsScheduler
// ========================
// Drop-in replacement for NrSlUeMacSchedulerFixedMcs that supports
// runtime MCS changes without any contrib modifications.
//
// Per-flow MCS tracking:
//   The scheduler now tracks MCS per (dstL2Id, lcId) instead of just per
//   dstL2Id. This allows different flows (identified by LC ID) to have
//   different MCS values even when they share the same destination L2 ID.
//
// HARQ process to LC ID mapping:
//   The scheduler emits a trace when resources are allocated, providing
//   the HARQ process ID and associated LC IDs. This enables per-flow
//   adaptation by linking HARQ feedback to specific flows.
//
// Timing requirement:
//   The parent's LogicalChannelPrioritization() reads dstInfo->GetDstMcs()
//   *before* DoNrSlAllocation runs, and uses it in a do/while sizing loop
//   that picks N_subch to fit the TBS. If we only update MCS inside
//   DoNrSlAllocation, the candidate resource was sized for the *previous*
//   MCS — N_subch will be wrong after every transition.
//
//   Fix: SetCurrentMcs() pushes the new MCS into every dstInfo we have
//   ever seen, so the next LCP call sees the fresh value. We snapshot
//   dstInfo shared_ptrs (as weak_ptrs to avoid extending lifetime) the
//   first time each one is passed to DoNrSlAllocation.

#include "ns3/nr-sl-ue-mac-scheduler-dst-info.h"
#include "ns3/nr-sl-ue-mac-scheduler-fixed-mcs.h"
#include "ns3/object.h"

#include <list>
#include <map>
#include <memory>
#include <set>
#include <unordered_set>
#include <utility>
#include <vector>

namespace ns3
{

class NrSlAdaptiveMcsScheduler : public NrSlUeMacSchedulerFixedMcs
{
  public:
    static TypeId GetTypeId();

    NrSlAdaptiveMcsScheduler() = default;
    ~NrSlAdaptiveMcsScheduler() override = default;

    // Called by the controller every evalIntervalMs.
    // Eagerly propagates to all dstInfos we've seen so the next
    // LogicalChannelPrioritization sees the fresh MCS in its sizing loop.
    // This sets MCS for all LCs of the destination (backward compatible).
    void SetCurrentMcs(uint8_t mcs);

    // Set MCS for a specific flow (identified by dstL2Id and lcId).
    // This allows per-flow MCS adaptation.
    void SetFlowMcs(uint32_t dstL2Id, uint8_t lcId, uint8_t mcs);

    // Get MCS for a specific flow. Returns the default MCS if not set.
    uint8_t GetFlowMcs(uint32_t dstL2Id, uint8_t lcId) const;

    // Get the current default MCS (used when no per-flow MCS is set).
    uint8_t GetCurrentMcs() const { return m_adaptiveMcs; }

    /**
     * Set a static per-destination MCS override (keyed by dstL2Id only).
     *
     * This bypasses all adaptive logic and per-flow MCS maps.  Call before
     * Simulator::Run().  The override persists for the whole simulation and
     * is applied inside DoNrSlAllocation BEFORE calling the parent, so the
     * parent (NrSlUeMacSchedulerFixedMcs) always allocates the minimum number
     * of sub-channels for the override MCS — identical behaviour to the fixed
     * scheduler but with per-destination granularity.
     *
     * Use case: split-groupcast with two different fixed MCS values, one per
     * receiver group, without modifying any ns-3-dev file.
     */
    void SetDstMcsOverride(uint32_t dstL2Id, uint8_t mcs);

    // TracedCallback signature for HARQ process allocation with LC IDs
    // Parameters: harqId, dstL2Id, lcIds (set of LC IDs in this HARQ process)
    typedef void (*HarqAllocationTracedCallback)(uint8_t harqId,
                                                  uint32_t dstL2Id,
                                                  const std::unordered_set<uint8_t>& lcIds);

  protected:
    bool DoNrSlAllocation(const std::list<SlResourceInfo>& candResources,
                          const std::shared_ptr<NrSlUeMacSchedulerDstInfo>& dstInfo,
                          std::set<SlGrantResource>& slotAllocList,
                          const AllocationInfo& allocationInfo) override;

  private:
    uint8_t m_adaptiveMcs{14};
    // Tracks every dstInfo seen via DoNrSlAllocation so SetCurrentMcs
    // can push the new MCS into it eagerly (before next LCP sizing loop).
    std::map<uint32_t, std::weak_ptr<NrSlUeMacSchedulerDstInfo>> m_seenDsts;

    // Per-flow MCS tracking: key = (dstL2Id, lcId), value = MCS
    // This allows different flows to have different MCS values.
    std::map<std::pair<uint32_t, uint8_t>, uint8_t> m_flowMcs;

    // Static per-destination MCS overrides: key = dstL2Id, value = MCS.
    // Set via SetDstMcsOverride() before Simulator::Run().  Applied in
    // DoNrSlAllocation before the per-flow / adaptive logic.
    std::map<uint32_t, uint8_t> m_dstMcsOverride;

    // Trace for HARQ process allocation with LC IDs
    TracedCallback<uint8_t, uint32_t, const std::unordered_set<uint8_t>&> m_harqAllocationTrace;
};

} // namespace ns3
