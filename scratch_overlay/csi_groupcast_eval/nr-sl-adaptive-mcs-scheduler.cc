// SPDX-License-Identifier: GPL-2.0-only
#include "nr-sl-adaptive-mcs-scheduler.h"

#include "ns3/log.h"
#include "ns3/uinteger.h"

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
            .AddConstructor<NrSlAdaptiveMcsScheduler>();
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

bool
NrSlAdaptiveMcsScheduler::DoNrSlAllocation(
    const std::list<SlResourceInfo>& candResources,
    const std::shared_ptr<NrSlUeMacSchedulerDstInfo>& dstInfo,
    std::set<SlGrantResource>& slotAllocList,
    const AllocationInfo& allocationInfo)
{
    // Defensive: also set on the dstInfo here in case this is the very
    // first allocation for this destination (not yet in m_seenDsts).
    dstInfo->SetDstMcs(m_adaptiveMcs);
    // Remember this dstInfo so SetCurrentMcs can push future MCS changes
    // into it before the next sizing loop runs.
    m_seenDsts[dstInfo->GetDstL2Id()] = dstInfo;
    return NrSlUeMacSchedulerFixedMcs::DoNrSlAllocation(
        candResources, dstInfo, slotAllocList, allocationInfo);
}

} // namespace ns3
