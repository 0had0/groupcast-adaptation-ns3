// SPDX-License-Identifier: GPL-2.0-only
#include "nr-sl-deterministic-scheduler.h"

#include "ns3/boolean.h"
#include "ns3/log.h"
#include "ns3/uinteger.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("NrSlDeterministicScheduler");
NS_OBJECT_ENSURE_REGISTERED(NrSlDeterministicScheduler);

TypeId
NrSlDeterministicScheduler::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::NrSlDeterministicScheduler")
            .SetParent<NrSlUeMacSchedulerFixedMcs>()
            .SetGroupName("Nr")
            .AddConstructor<NrSlDeterministicScheduler>()
            .AddAttribute("LaneStart",
                          "First subchannel of this UE's FDM lane.",
                          UintegerValue(0),
                          MakeUintegerAccessor(&NrSlDeterministicScheduler::m_laneStart),
                          MakeUintegerChecker<uint8_t>())
            .AddAttribute("LaneLength",
                          "Number of subchannels in this UE's FDM lane (0 = no FDM limit).",
                          UintegerValue(0),
                          MakeUintegerAccessor(&NrSlDeterministicScheduler::m_laneLength),
                          MakeUintegerChecker<uint8_t>())
            .AddAttribute("SlotModulus",
                          "TDM period in SL slots (0 = no TDM limit).",
                          UintegerValue(0),
                          MakeUintegerAccessor(&NrSlDeterministicScheduler::m_slotModulus),
                          MakeUintegerChecker<uint32_t>())
            .AddAttribute("SlotPhase",
                          "TDM phase: allow slots with Normalize()%modulus==phase.",
                          UintegerValue(0),
                          MakeUintegerAccessor(&NrSlDeterministicScheduler::m_slotPhase),
                          MakeUintegerChecker<uint32_t>())
            .AddAttribute("Strict",
                          "If true, skip the slot when no candidate fits the partition.",
                          BooleanValue(true),
                          MakeBooleanAccessor(&NrSlDeterministicScheduler::m_strict),
                          MakeBooleanChecker());
    return tid;
}

void
NrSlDeterministicScheduler::SetSubchannelLane(uint8_t start, uint8_t length)
{
    m_laneStart = start;
    m_laneLength = length;
    NS_LOG_INFO("FDM lane = [" << +start << ", " << +(start + length) << ")");
}

void
NrSlDeterministicScheduler::SetSlotPartition(uint32_t modulus, uint32_t phase)
{
    m_slotModulus = modulus;
    m_slotPhase = (modulus > 0) ? (phase % modulus) : 0;
    NS_LOG_INFO("TDM partition: slot % " << modulus << " == " << m_slotPhase);
}

void
NrSlDeterministicScheduler::SetStrict(bool strict)
{
    m_strict = strict;
}

void
NrSlDeterministicScheduler::SetDstMcsOverride(uint32_t dstL2Id, uint8_t mcs)
{
    m_dstMcsOverride[dstL2Id] = mcs;
}

std::pair<uint8_t, uint8_t>
NrSlDeterministicScheduler::Lane(uint8_t totalSubch, uint8_t numLanes, uint8_t laneIdx)
{
    if (numLanes == 0)
    {
        return {0, totalSubch};
    }
    uint8_t width = totalSubch / numLanes;
    if (width == 0)
    {
        width = 1;
    }
    uint8_t start = static_cast<uint8_t>(laneIdx % numLanes) * width;
    if (start >= totalSubch)
    {
        start = 0;
    }
    // last lane absorbs the remainder so the whole pool is covered
    uint8_t len = (laneIdx == numLanes - 1) ? static_cast<uint8_t>(totalSubch - start) : width;
    return {start, len};
}

bool
NrSlDeterministicScheduler::InLane(const SlResourceInfo& r) const
{
    if (m_laneLength == 0)
    {
        return true; // no FDM restriction
    }
    uint16_t end = static_cast<uint16_t>(r.slSubchannelStart) + r.slSubchannelLength;
    return (r.slSubchannelStart >= m_laneStart) &&
           (end <= static_cast<uint16_t>(m_laneStart) + m_laneLength);
}

bool
NrSlDeterministicScheduler::InSlotPhase(const SlResourceInfo& r) const
{
    if (m_slotModulus == 0)
    {
        return true; // no TDM restriction
    }
    return (r.sfn.Normalize() % m_slotModulus) == m_slotPhase;
}

bool
NrSlDeterministicScheduler::DoNrSlAllocation(
    const std::list<SlResourceInfo>& candResources,
    const std::shared_ptr<NrSlUeMacSchedulerDstInfo>& dstInfo,
    std::set<SlGrantResource>& slotAllocList,
    const AllocationInfo& allocationInfo)
{
    ++m_attempts;

    // Per-destination fixed MCS (applied before the parent sizes the grant).
    auto ov = m_dstMcsOverride.find(dstInfo->GetDstL2Id());
    if (ov != m_dstMcsOverride.end())
    {
        dstInfo->SetDstMcs(ov->second);
    }

    const bool noRestriction = (m_laneLength == 0 && m_slotModulus == 0);
    if (noRestriction)
    {
        return NrSlUeMacSchedulerFixedMcs::DoNrSlAllocation(candResources,
                                                            dstInfo,
                                                            slotAllocList,
                                                            allocationInfo);
    }

    // Keep only candidates inside this UE's deterministic partition.
    std::list<SlResourceInfo> filtered;
    for (const auto& r : candResources)
    {
        if (InLane(r) && InSlotPhase(r))
        {
            filtered.push_back(r);
        }
    }

    if (filtered.empty())
    {
        // No resource in our partition this round.
        if (m_strict || candResources.empty())
        {
            ++m_skipped;
            NS_LOG_DEBUG("No candidate in partition; skipping (strict).");
            return false;
        }
        // Non-strict: fall back to the full set (re-introduces SB-SPS contention).
        return NrSlUeMacSchedulerFixedMcs::DoNrSlAllocation(candResources,
                                                            dstInfo,
                                                            slotAllocList,
                                                            allocationInfo);
    }

    return NrSlUeMacSchedulerFixedMcs::DoNrSlAllocation(filtered,
                                                        dstInfo,
                                                        slotAllocList,
                                                        allocationInfo);
}

} // namespace ns3
