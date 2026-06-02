// SPDX-License-Identifier: GPL-2.0-only
#include "nr-sl-adaptive-mcs-scheduler.h"

#include "ns3/log.h"
#include "ns3/uinteger.h"

NS_LOG_COMPONENT_DEFINE("NrSlAdaptiveMcsController");

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("NrSlAdaptiveMcsScheduler");
NS_OBJECT_ENSURE_REGISTERED(NrSlAdaptiveMcsScheduler);

TypeId
NrSlAdaptiveMcsScheduler::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::NrSlAdaptiveMcsScheduler")
            .SetParent<NrSlUeMacSchedulerFixedMcs>()
            .SetGroupName("Nr")
            .AddConstructor<NrSlAdaptiveMcsScheduler>()
            .AddTraceSource("HarqAllocation",
                            "Trace HARQ process allocation with LC IDs",
                            MakeTraceSourceAccessor(&NrSlAdaptiveMcsScheduler::m_harqAllocationTrace),
                            "ns3::NrSlAdaptiveMcsScheduler::HarqAllocationTracedCallback");
    return tid;
}

void
NrSlAdaptiveMcsScheduler::SetCurrentMcs(uint8_t mcs)
{
    m_adaptiveMcs = mcs;
    // Push to all known dstInfos so the next LCP sizing loop reads the
    // fresh value and picks the right N_subch for the new MCS.
    for (auto it = m_seenDsts.begin(); it != m_seenDsts.end();)
    {
        if (auto sp = it->second.lock())
        {
            sp->SetDstMcs(mcs);
            ++it;
        }
        else
        {
            it = m_seenDsts.erase(it); // dstInfo gone, drop the entry
        }
    }
}

void
NrSlAdaptiveMcsScheduler::SetFlowMcs(uint32_t dstL2Id, uint8_t lcId, uint8_t mcs)
{
    m_flowMcs[std::make_pair(dstL2Id, lcId)] = mcs;
    // Also update the dstInfo's default MCS for backward compatibility
    // with the LCP sizing loop.
    auto it = m_seenDsts.find(dstL2Id);
    if (it != m_seenDsts.end())
    {
        if (auto sp = it->second.lock())
        {
            sp->SetDstMcs(mcs);
        }
    }
}

uint8_t
NrSlAdaptiveMcsScheduler::GetFlowMcs(uint32_t dstL2Id, uint8_t lcId) const
{
    auto it = m_flowMcs.find(std::make_pair(dstL2Id, lcId));
    if (it != m_flowMcs.end())
    {
        return it->second;
    }
    // Return default MCS if no per-flow MCS is set
    return m_adaptiveMcs;
}

void
NrSlAdaptiveMcsScheduler::SetDstMcsOverride(uint32_t dstL2Id, uint8_t mcs)
{
    m_dstMcsOverride[dstL2Id] = mcs;
}

bool
NrSlAdaptiveMcsScheduler::DoNrSlAllocation(
    const std::list<SlResourceInfo>& candResources,
    const std::shared_ptr<NrSlUeMacSchedulerDstInfo>& dstInfo,
    std::set<SlGrantResource>& slotAllocList,
    const AllocationInfo& allocationInfo)
{
    // ── Static per-destination MCS override (set via SetDstMcsOverride) ──
    // This takes highest priority and bypasses all adaptive / per-flow logic.
    // The MCS is applied to dstInfo BEFORE calling the parent so that
    // NrSlUeMacSchedulerFixedMcs::DoNrSlAllocation always allocates the
    // minimum sub-channels for the correct MCS.
    {
        auto ovIt = m_dstMcsOverride.find(dstInfo->GetDstL2Id());
        if (ovIt != m_dstMcsOverride.end())
        {
            m_seenDsts[dstInfo->GetDstL2Id()] = dstInfo;
            dstInfo->SetDstMcs(ovIt->second);

            bool allocated = NrSlUeMacSchedulerFixedMcs::DoNrSlAllocation(
                candResources, dstInfo, slotAllocList, allocationInfo);

            if (allocated)
            {
                std::unordered_set<uint8_t> lcIds;
                for (const auto& rlcPdu : allocationInfo.m_allocatedRlcPdus)
                    lcIds.insert(rlcPdu.lcid);
                if (!lcIds.empty())
                    m_harqAllocationTrace(0, dstInfo->GetDstL2Id(), lcIds);
            }
            return allocated;
        }
    }

    // Defensive: also set on the dstInfo here in case this is the very
    // first allocation for this destination (not yet in m_seenDsts).
    m_seenDsts[dstInfo->GetDstL2Id()] = dstInfo;

    // Determine the MCS to use for this allocation
    // Check if all LCs in this allocation have the same per-flow MCS
    uint8_t allocationMcs = m_adaptiveMcs; // Default
    bool hasPerFlowMcs = false;
    uint8_t firstPerFlowMcs = 0;

    for (const auto& rlcPdu : allocationInfo.m_allocatedRlcPdus)
    {
        uint8_t lcId = rlcPdu.lcid;
        uint8_t flowMcs = GetFlowMcs(dstInfo->GetDstL2Id(), lcId);
        if (flowMcs != m_adaptiveMcs)
        {
            // This LC has a per-flow MCS
            if (!hasPerFlowMcs)
            {
                firstPerFlowMcs = flowMcs;
                hasPerFlowMcs = true;
            }
            else if (flowMcs != firstPerFlowMcs)
            {
                // Different LCs have different per-flow MCS values
                // Fall back to default MCS for this allocation
                allocationMcs = m_adaptiveMcs;
                hasPerFlowMcs = false;
                break;
            }
            else
            {
                // All LCs so far have the same per-flow MCS
                allocationMcs = flowMcs;
            }
        }
    }

    if (hasPerFlowMcs)
    {
        allocationMcs = firstPerFlowMcs;
    }

    // Set the MCS in dstInfo so the parent uses it
    dstInfo->SetDstMcs(allocationMcs);

    // Call parent implementation to perform the allocation
    bool allocated = NrSlUeMacSchedulerFixedMcs::DoNrSlAllocation(
        candResources, dstInfo, slotAllocList, allocationInfo);

    // If allocation succeeded, emit trace for HARQ mapping
    if (allocated)
    {
        // Collect LC IDs from this allocation
        std::unordered_set<uint8_t> lcIds;
        for (const auto& rlcPdu : allocationInfo.m_allocatedRlcPdus)
        {
            lcIds.insert(rlcPdu.lcid);
        }

        // Emit trace for HARQ process to LC ID mapping
        if (!lcIds.empty())
        {
            m_harqAllocationTrace(0, dstInfo->GetDstL2Id(), lcIds);
        }
    }

    return allocated;
}

} // namespace ns3
