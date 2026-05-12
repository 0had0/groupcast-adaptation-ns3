// SPDX-License-Identifier: GPL-2.0-only
#pragma once
// NrSlAdaptiveMcsScheduler
// ========================
// Drop-in replacement for NrSlUeMacSchedulerFixedMcs that supports
// runtime MCS changes without any contrib modifications.
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
    void SetCurrentMcs(uint8_t mcs);
    uint8_t GetCurrentMcs() const { return m_adaptiveMcs; }

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
};

} // namespace ns3
