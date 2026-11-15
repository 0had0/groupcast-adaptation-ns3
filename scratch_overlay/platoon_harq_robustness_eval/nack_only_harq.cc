// nack_only_harq.cc -- see nack_only_harq.h

#include "nack_only_harq.h"

#include "ns3/log.h"
#include "ns3/node.h"
#include "ns3/lte-radio-bearer-tag.h"
#include "ns3/nr-sl-sci-f2a-header.h"
#include "ns3/nr-spectrum-phy.h"
#include "ns3/nr-ue-phy.h"
#include "ns3/simulator.h"

#include <cmath>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("PlatoonNackOnlyHarq");

// ---------------------------------------------------------------------------
// PlatoonSeqTag
// ---------------------------------------------------------------------------

NS_OBJECT_ENSURE_REGISTERED(PlatoonSeqTag);

TypeId
PlatoonSeqTag::GetTypeId()
{
    static TypeId tid = TypeId("ns3::PlatoonSeqTag")
                            .SetParent<Tag>()
                            .SetGroupName("NrV2xCommon")
                            .AddConstructor<PlatoonSeqTag>();
    return tid;
}

TypeId
PlatoonSeqTag::GetInstanceTypeId() const
{
    return GetTypeId();
}

uint32_t
PlatoonSeqTag::GetSerializedSize() const
{
    return 4;
}

void
PlatoonSeqTag::Serialize(TagBuffer i) const
{
    i.WriteU32(m_seq);
}

void
PlatoonSeqTag::Deserialize(TagBuffer i)
{
    m_seq = i.ReadU32();
}

void
PlatoonSeqTag::Print(std::ostream& os) const
{
    os << "seq=" << m_seq;
}

// ---------------------------------------------------------------------------
// SAP wrappers (leader only)
// ---------------------------------------------------------------------------

namespace
{

class UserWrap : public NrSlUePhySapUser
{
  public:
    UserWrap(NrSlUePhySapUser* orig, NackOnlyHarqController* ctl, size_t dev)
        : m_orig(orig),
          m_ctl(ctl),
          m_dev(dev)
    {
    }

    uint8_t GetSlActiveTxPoolId() override
    {
        return m_orig->GetSlActiveTxPoolId();
    }

    std::vector<std::pair<uint32_t, uint8_t>> GetSlTxDestinations() override
    {
        return m_orig->GetSlTxDestinations();
    }

    std::unordered_set<uint32_t> GetSlRxDestinations() override
    {
        return m_orig->GetSlRxDestinations();
    }

    void ReceivePsschPhyPdu(Ptr<PacketBurst> pdu) override
    {
        m_orig->ReceivePsschPhyPdu(pdu);
    }

    void ReceiveSensingData(SensingData sensingData) override
    {
        m_orig->ReceiveSensingData(sensingData);
    }

    void ReceivePsfch(uint32_t sendingNodeId, SlHarqInfo harqInfo) override
    {
        m_ctl->OnPsfch(m_dev, sendingNodeId, harqInfo);
        if (m_ctl->GetMode() != NackOnlyHarqController::HarqMode::OPT2)
        {
            m_orig->ReceivePsfch(sendingNodeId, harqInfo);
        }
    }

  private:
    NrSlUePhySapUser* m_orig;
    NackOnlyHarqController* m_ctl;
    size_t m_dev;
};

class ProvWrap : public NrSlUePhySapProvider
{
  public:
    ProvWrap(NrSlUePhySapProvider* orig, NackOnlyHarqController* ctl, size_t dev)
        : m_orig(orig),
          m_ctl(ctl),
          m_dev(dev)
    {
    }

    uint32_t GetBwInRbs() const override
    {
        return m_orig->GetBwInRbs();
    }

    Time GetSlotPeriod() const override
    {
        return m_orig->GetSlotPeriod();
    }

    void SendPscchMacPdu(Ptr<Packet> p) override
    {
        m_orig->SendPscchMacPdu(p);
    }

    void SendPsschMacPdu(Ptr<Packet> p, uint32_t dstL2Id) override
    {
        m_ctl->OnPsschPdu(m_dev, p, dstL2Id);
        m_orig->SendPsschMacPdu(p, dstL2Id);
    }

    void SetNrSlVarTtiAllocInfo(const SfnSf& sfn, const NrSlVarTtiAllocInfo& varTtiInfo) override
    {
        m_orig->SetNrSlVarTtiAllocInfo(sfn, varTtiInfo);
    }

  private:
    NrSlUePhySapProvider* m_orig;
    NackOnlyHarqController* m_ctl;
    size_t m_dev;
};

} // namespace

// ---------------------------------------------------------------------------
// NackOnlyHarqController
// ---------------------------------------------------------------------------

void
NackOnlyHarqController::Install(Ptr<NrUeNetDevice> leader,
                                const std::vector<Ptr<NrUeNetDevice>>& members,
                                uint32_t dstL2Id,
                                HarqMode mode,
                                uint8_t minTimeGapPsfch)
{
    m_leader = leader;
    m_members = members;
    m_dst = dstL2Id;
    m_mode = mode;
    m_gap = minTimeGapPsfch;

    m_mac = leader->GetMac(0)->GetObject<NrSlUeMac>();
    NS_ABORT_MSG_IF(!m_mac, "leader has no NrSlUeMac");
    Ptr<NrUePhy> phy = leader->GetPhy(0);
    m_slot = phy->GetSlotPeriod();
    m_sym = phy->GetSymbolPeriod();
    m_mu = static_cast<uint8_t>(std::lround(std::log2(1e-3 / m_slot.GetSeconds())));
    m_origUser = m_mac->GetNrSlUePhySapUser();
    TimeValue tv;
    phy->GetAttribute("SlPsfchProcessingDelay", tv);
    m_psfchDelay = tv.Get().IsNegative() ? m_slot : tv.Get();
    m_mac->GetAttribute("CoopActivationDelay", tv);
    m_activation = tv.Get();
    Attach(leader, leader->GetNode()->GetId());
}

void
NackOnlyHarqController::Install(Ptr<NrUeNetDevice> leader,
                                const std::vector<Ptr<NrUeNetDevice>>& members,
                                uint32_t dstL2Id,
                                bool option1Tx,
                                uint8_t minTimeGapPsfch)
{
    Install(leader, members, dstL2Id, option1Tx ? HarqMode::OPT1 : HarqMode::BLIND, minTimeGapPsfch);
}

void
NackOnlyHarqController::AddObservedDevice(Ptr<NrUeNetDevice> dev, uint32_t nodeIndex)
{
    NS_ABORT_MSG_IF(m_mode != HarqMode::BLIND, "only observe-only controllers record several devices");
    Attach(dev, nodeIndex);
}

void
NackOnlyHarqController::Attach(Ptr<NrUeNetDevice> dev, uint32_t nodeIndex)
{
    const size_t k = m_macs.size();
    Ptr<NrSlUeMac> mac = dev->GetMac(0)->GetObject<NrSlUeMac>();
    NS_ABORT_MSG_IF(!mac, "device has no NrSlUeMac");
    Ptr<NrUePhy> phy = dev->GetPhy(0);
    m_macs.push_back(mac);
    m_devNode.push_back(nodeIndex);
    m_pendingSeqs.emplace_back();
    m_userWraps.push_back(std::make_unique<UserWrap>(mac->GetNrSlUePhySapUser(), this, k));
    m_provWraps.push_back(std::make_unique<ProvWrap>(phy->GetNrSlUePhySapProvider(), this, k));
    phy->SetNrSlUePhySapUser(m_userWraps.back().get());
    mac->SetNrSlUePhySapProvider(m_provWraps.back().get());
    mac->TraceConnectWithoutContext(
        "SlPsschScheduling",
        MakeCallback(&NackOnlyHarqController::OnPsschScheduling, this).Bind(k));
}

void
NackOnlyHarqController::TagAppPacket(Ptr<Packet> p)
{
    PlatoonSeqTag tag;
    tag.SetSeq(m_nextSeq++);
    p->AddByteTag(tag);
}

void
NackOnlyHarqController::OnPsschPdu(size_t dev, Ptr<Packet> p, uint32_t dstL2Id)
{
    if (dstL2Id != m_dst)
    {
        return;
    }
    // the SCI-2A of this PSSCH travels as its own packet (no bearer tag, the header only):
    // the helper it names for the transmitter's own TB (Option 2 with a helper)
    LteRadioBearerTag bearer;
    if ((m_mode == HarqMode::OPT2 || m_opt1HelperNack) && !p->PeekPacketTag(bearer))
    {
        NrSlSciF2aHeader sci;
        if (p->GetSize() > 0 && p->PeekHeader(sci) == p->GetSize())
        {
            m_pendingHelper[dev] =
                sci.GetCoopInfo().relayedFromRnti == 0 ? sci.GetCoopInfo().helperId : 0;
        }
        return;
    }
    ByteTagIterator it = p->GetByteTagIterator();
    while (it.HasNext())
    {
        ByteTagIterator::Item item = it.Next();
        if (item.GetTypeId() == PlatoonSeqTag::GetTypeId())
        {
            PlatoonSeqTag tag;
            item.GetTag(tag);
            m_pendingSeqs[dev].insert(tag.GetSeq());
        }
    }
}

void
NackOnlyHarqController::OnPsschScheduling(size_t dev, SlPsschUeMacStatParameters p)
{
    if (p.dstL2Id != m_dst)
    {
        m_pendingSeqs[dev].clear();
        return;
    }
    const double now = Simulator::Now().GetSeconds();
    const double ts = m_slot.GetSeconds();
    const double frac = now / ts - std::floor(now / ts + 1e-9);
    if (frac > 1e-6 && frac < 1.0 - 1e-6)
    {
        ++nSlotMisaligned;
    }

    LeaderTx tx;
    tx.tS = now;
    tx.slotAbs = std::llround(now / ts);
    tx.frame = static_cast<uint16_t>(p.frameNum);
    tx.subframe = static_cast<uint8_t>(p.subframeNum);
    tx.slot = p.slotNum;
    tx.rbStart = p.rbStart;
    tx.rbLen = p.rbLength;
    tx.harqId = p.harqId;
    tx.ndi = p.ndi;
    tx.rv = p.rv;
    tx.seqs.assign(m_pendingSeqs[dev].begin(), m_pendingSeqs[dev].end());
    m_pendingSeqs[dev].clear();
    tx.node = m_devNode[dev];
    tx.rnti = p.rnti;
    if ((m_mode == HarqMode::OPT2 || m_opt1HelperNack) && m_helperTakeover)
    {
        // the helper this transmission's SCI-2A names (sent just before this trace)
        tx.helperRnti = m_pendingHelper.count(dev) ? m_pendingHelper[dev] : 0;
        m_pendingHelper.erase(dev);
        for (const auto& m : m_members)
        {
            if (tx.helperRnti != 0 && m->GetMac(0)->GetObject<NrSlUeMac>()->GetRnti() == tx.helperRnti)
            {
                tx.helperNode = m->GetNode()->GetId();
            }
        }
        nHelperNamedTx += tx.helperNode != 0 ? 1 : 0;
    }

    const size_t idx = m_txs.size();
    // a helper's relayed copies all have ndi = 0: a copy carrying other packets
    // than the last transmission of this HARQ id is a new TB (the leader's
    // retransmissions carry the same packets, or none)
    auto lastOfHarq = m_lastTxOfHarq.find(tx.harqId);
    const bool otherPackets = !tx.seqs.empty() && lastOfHarq != m_lastTxOfHarq.end() &&
                              m_txs[lastOfHarq->second].seqs != tx.seqs;
    if (tx.ndi == 1 || !m_curGen.count(tx.harqId) || otherPackets)
    {
        m_curGen[tx.harqId] = static_cast<uint32_t>(idx);
        tx.gen = static_cast<uint32_t>(idx);
        tx.txInTb = 1;
    }
    else
    {
        tx.gen = m_curGen[tx.harqId];
        auto last = m_lastTxOfHarq.find(tx.harqId);
        tx.txInTb = (last != m_lastTxOfHarq.end()) ? m_txs[last->second].txInTb + 1 : 2;
        if (tx.seqs.empty() && last != m_lastTxOfHarq.end())
        {
            tx.seqs = m_txs[last->second].seqs;
        }
    }
    if (tx.txInTb == 1)
    {
        m_pendingOrigin.erase(tx.harqId);
    }
    else if (dev == 0)
    {
        // a leader retransmission in a handed slot it took back
        auto po = m_pendingOrigin.find(tx.harqId);
        if (po != m_pendingOrigin.end())
        {
            tx.origin = po->second;
            m_pendingOrigin.erase(po);
        }
    }
    m_lastTxOfHarq[tx.harqId] = idx;
    m_txs.push_back(tx);

    // PSFCH occasion of this transmission
    SfnSf sfn(p.frameNum, static_cast<uint8_t>(p.subframeNum), static_cast<uint8_t>(p.slotNum),
              m_mu);
    uint32_t k = m_gap;
    SfnSf psfch = sfn.GetFutureSfnSf(m_gap);
    while (!m_mac->SlotHasPsfch(psfch) && k < 64)
    {
        psfch.Add(1);
        ++k;
    }
    // NACKs reach the MAC at  t(m) + 13 sym (end of PSFCH) + the PSFCH processing delay
    // (default one slot); decide half a symbol later.
    const Time decideAt = m_slot * static_cast<int64_t>(k) + m_sym * 13 + m_psfchDelay + m_sym / 2;
    m_txs[idx].decideAtS = (Simulator::Now() + decideAt).GetSeconds();
    Simulator::Schedule(decideAt, &NackOnlyHarqController::Decide, this, idx);
}

void
NackOnlyHarqController::OnPsfch(size_t dev, uint32_t nodeId, const SlHarqInfo& info)
{
    if (info.m_dstL2Id != m_dst || info.m_txRnti != m_macs[dev]->GetRnti())
    {
        return;
    }
    const bool isAck = info.IsReceivedOk();
    if (isAck)
    {
        ++nAckRx;
    }
    else
    {
        ++nNackRx;
    }
    // attribute feedback to the pending transmission of this HARQ id whose
    // PSFCH occasion this is: the one decided next.
    const double now = Simulator::Now().GetSeconds();
    // exact binding: this PSFCH belongs to the transmission decided within the next slot
    const double slotS = m_slot.GetSeconds();
    LeaderTx* best = nullptr;
    for (size_t i = m_txs.size(); i-- > 0;)
    {
        LeaderTx& tx = m_txs[i];
        const bool inOccasion =
            !m_exactPsfch || (tx.decideAtS >= now - 1e-9 && tx.decideAtS - now < slotS);
        if (tx.harqId == info.m_harqProcessId && tx.decision < 0 && tx.tS < now &&
            tx.node == m_devNode[dev] && inOccasion && (!best || tx.decideAtS < best->decideAtS))
        {
            best = &tx;
        }
        if (now - tx.tS > 0.1)
        {
            break; // older than any pending decision
        }
    }
    if (!best && m_exactPsfch)
    {
        ++nFbLate;
        return;
    }
    // the named helper's NACK (Option 2: its own PSFCH resource; Option 1 with
    // m_opt1HelperNack: a dedicated helper resource next to the shared NACK one)
    if (best && (m_mode == HarqMode::OPT2 || m_opt1HelperNack) && best->helperNode != 0 &&
        nodeId == best->helperNode)
    {
        if (isAck)
        {
            ++nHelperAck; // must not happen: the named helper stays silent on success
        }
        else if (m_exactPsfch && m_curGen[best->harqId] != best->gen)
        {
            // the HARQ id already carries the next TB: a reclaim would take its slot
            ++nHelperNackStale;
        }
        else if (!best->helperNack)
        {
            // the helper did not get the TB: the leader retransmits in the next slot it
            // had handed over (the helper listens to it and may relay after it)
            best->helperNack = true;
            ++nHelperNack;
            if (ReclaimInTime(dev, *best, nTakeoverLate, nTakeoverLateNoSlot))
            {
                best->helperReclaim = true;
                ++nHelperReclaim;
                m_pendingOrigin[best->harqId] = 1;
            }
            else
            {
                ++nHelperNackNoSlot;
            }
        }
    }
    if (best)
    {
        if (isAck)
        {
            best->ackedMembers.insert(nodeId);
        }
        else
        {
            if (best->nNack < 255)
            {
                ++best->nNack;
            }
            best->nackedMembers.insert(nodeId);
        }
    }
}

int64_t
NackOnlyHarqController::AbsOfSfn(const LeaderTx& tx, const SfnSf& s) const
{
    const SfnSf txSfn(tx.frame, tx.subframe, static_cast<uint8_t>(tx.slot), m_mu);
    int64_t d = static_cast<int64_t>(s.Normalize()) - static_cast<int64_t>(txSfn.Normalize());
    const int64_t wrap = 1024LL * 10 * (1LL << m_mu);
    if (d < -wrap / 2)
    {
        d += wrap;
    }
    return tx.slotAbs + d;
}

SfnSf
NackOnlyHarqController::SfnOfAbs(const LeaderTx& tx, int64_t abs) const
{
    SfnSf s(tx.frame, tx.subframe, static_cast<uint8_t>(tx.slot), m_mu);
    if (abs > tx.slotAbs)
    {
        s.Add(static_cast<uint32_t>(abs - tx.slotAbs));
    }
    return s;
}

int64_t
NackOnlyHarqController::EarliestActionSlot() const
{
    const double x = (Simulator::Now() + m_activation).GetSeconds() / m_slot.GetSeconds();
    return static_cast<int64_t>(std::ceil(x - 1e-9));
}

bool
NackOnlyHarqController::ReclaimInTime(size_t dev,
                                      const LeaderTx& tx,
                                      uint64_t& nLate,
                                      uint64_t& nLateNoSlot)
{
    const int64_t earliest = EarliestActionSlot();
    SfnSf next;
    const bool late = m_macs[dev]->GetSlNextHelperSlot(m_dst, tx.harqId, next) &&
                      AbsOfSfn(tx, next) < earliest;
    const bool ok = m_macs[dev]->ReclaimSlHelperSlot(m_dst, tx.harqId, SfnOfAbs(tx, earliest));
    if (late)
    {
        ++(ok ? nLate : nLateNoSlot);
    }
    return ok;
}

void
NackOnlyHarqController::Succeed(size_t idx)
{
    const LeaderTx& tx = m_txs[idx];
    SfnSf next;
    bool usurped = false;
    if (m_mac->GetSlNextGrantSlot(m_dst, tx.harqId, next, usurped) && !usurped)
    {
        const int64_t abs = AbsOfSfn(tx, next);
        if (abs < EarliestActionSlot())
        {
            // too late to withhold the next own (prepared) copy: it goes out, the success
            // cancels what remains after it
            ++nCancelLate;
            const Time at = m_slot * abs + m_sym / 2 - Simulator::Now();
            Simulator::Schedule(at, &NackOnlyHarqController::DeliverSuccessLate, this, idx);
            return;
        }
    }
    DeliverSuccess(tx);
}

void
NackOnlyHarqController::DeliverSuccessLate(size_t idx)
{
    const LeaderTx& tx = m_txs[idx];
    if (m_curGen[tx.harqId] != tx.gen)
    {
        return;
    }
    if (m_spsSafeAck && !m_mac->IsSlRetxPending(m_dst, tx.harqId))
    {
        return;
    }
    DeliverSuccess(tx);
}

void
NackOnlyHarqController::DeliverSuccess(const LeaderTx& tx)
{
    const uint16_t txRnti = m_mac->GetRnti();
    for (const auto& m : m_members)
    {
        Ptr<NrSlUeMac> mmac = m->GetMac(0)->GetObject<NrSlUeMac>();
        SlHarqInfo ack;
        ack.m_rnti = mmac->GetRnti();
        ack.m_txRnti = txRnti;
        ack.m_dstL2Id = static_cast<uint16_t>(m_dst);
        ack.m_harqProcessId = tx.harqId;
        ack.m_bwpIndex = 0;
        ack.m_harqStatus = SlHarqInfo::ACK;
        m_origUser->ReceivePsfch(m->GetNode()->GetId(), ack);
    }
}

void
NackOnlyHarqController::Decide(size_t idx)
{
    LeaderTx& tx = m_txs[idx];
    if (m_curGen[tx.harqId] != tx.gen)
    {
        // the HARQ process already carries a newer TB: nothing to cancel
        tx.decision = 2;
        ++nStale;
        return;
    }
    if (m_mode == HarqMode::BLIND)
    {
        tx.decision = 0;
        return;
    }
    if (m_mode == HarqMode::OPT1)
    {
        if (tx.nNack > 0)
        {
            tx.decision = 0;
            ++nNackDecision;
            MaybeScheduleFallback(idx);
            return;
        }
        tx.decision = 1;
        ++nImplicitAck;
        if (m_spsSafeAck && !m_mac->IsSlRetxPending(m_dst, tx.harqId))
        {
            ++nAckNoRetxLeft;
            return;
        }
        Succeed(idx);
        return;
    }
    if (m_mode == HarqMode::OPT2)
    {
        // the selector's link statistics: the outcome of the TB's first transmission only
        // (the direct leader -> member link; a retransmission's ACK is a different trial)
        if (!m_fbCallback.IsNull() && tx.txInTb == 1)
        {
            const double nowS = Simulator::Now().GetSeconds();
            for (size_t k = 0; k < m_members.size(); ++k)
            {
                uint32_t nid = m_members[k]->GetNode()->GetId();
                if (nid == tx.helperNode)
                {
                    // the named helper never ACKs: its silence carries no information, and
                    // recording only its NACKs would bias its estimated link downwards (the
                    // selector would avoid a member because it was chosen). Skip it here.
                    continue;
                }
                uint8_t kind = 2; // DTX default (silent failure)
                if (tx.ackedMembers.count(nid) > 0)
                {
                    kind = 0; // ACK
                }
                else if (tx.nackedMembers.count(nid) > 0)
                {
                    kind = 1; // NACK
                }
                uint32_t memberIndex = static_cast<uint32_t>(k + 1);
                m_fbCallback(nowS, memberIndex, tx.gen, kind);
            }
        }
        // Option 2: success only if ALL members ACKed; DTX or any NACK counts as NACK
        // (the named helper never ACKs: success = every other member ACKed and nobody,
        // the helper included, NACKed)
        size_t expected = m_members.size();
        size_t acked = tx.ackedMembers.size();
        if (tx.helperNode != 0)
        {
            expected -= 1;
            acked -= tx.ackedMembers.count(tx.helperNode);
        }
        const bool allAck = (acked >= expected && tx.nNack == 0);
        if (allAck)
        {
            tx.decision = 1;
            ++nAllAckDecision;
            if (m_spsSafeAck && !m_mac->IsSlRetxPending(m_dst, tx.harqId))
            {
                ++nAckNoRetxLeft;
                return;
            }
            Succeed(idx);
        }
        else
        {
            tx.decision = 0;
            ++nNackDecision;
            MaybeScheduleFallback(idx);
        }
        return;
    }
}

void
NackOnlyHarqController::SetLeaderFallback(bool on)
{
    m_fallback = on && m_mode != HarqMode::BLIND;
    if (m_fallback)
    {
        m_leader->GetPhy(0)->GetSpectrumPhy()->TraceConnectWithoutContext(
            "RxPscchTraceUe",
            MakeCallback(&NackOnlyHarqController::OnLeaderPscch, this));
        m_leader->GetPhy(0)->GetSpectrumPhy()->TraceConnectWithoutContext(
            "SlRxSignal",
            MakeCallback(&NackOnlyHarqController::OnLeaderRxSignal, this));
    }
}

void
NackOnlyHarqController::OnLeaderPscch(SlRxCtrlPacketTraceParams p)
{
    const int64_t slotAbs =
        static_cast<int64_t>(std::floor(p.m_timeMs * 1e-3 / m_slot.GetSeconds() + 1e-9));
    if (p.m_corrupt)
    {
        // validation only: the protocol never reads it (a real UE cannot tell who sent it)
        std::set<int64_t>& failed = m_sciFailed[p.m_txRnti];
        failed.insert(slotAbs);
        while (!failed.empty() && *failed.begin() < slotAbs - 4000)
        {
            failed.erase(failed.begin());
        }
        return;
    }
    std::set<int64_t>& heard = m_sciHeard[p.m_txRnti];
    heard.insert(slotAbs);
    while (!heard.empty() && *heard.begin() < slotAbs - 4000)
    {
        heard.erase(heard.begin()); // older than 2 s at mu 1: no check looks back that far
    }
}

void
NackOnlyHarqController::MaybeScheduleFallback(size_t idx)
{
    const LeaderTx& tx = m_txs[idx];
    if (!m_fallback || tx.helperNode == 0 || tx.helperNack)
    {
        return; // no helper, or its NACK already took back a slot
    }
    SfnSf next;
    uint16_t sbStart = 0;
    uint16_t sbLen = 0;
    if (!m_mac->GetSlNextHelperSlot(m_dst, tx.harqId, next, sbStart, sbLen))
    {
        return; // no handed slot left: nothing the helper should relay in
    }
    const SfnSf txSfn(tx.frame, tx.subframe, static_cast<uint8_t>(tx.slot), m_mu);
    int64_t d = static_cast<int64_t>(next.Normalize()) - static_cast<int64_t>(txSfn.Normalize());
    const int64_t wrap = 1024LL * 10 * (1LL << m_mu);
    if (d < 0)
    {
        d += wrap;
    }
    const int64_t slotAbs = tx.slotAbs + d;
    const Time checkAt = m_slot * (slotAbs + 1) + m_sym / 2; // end of the helper's slot
    if (checkAt <= Simulator::Now())
    {
        return;
    }
    ++nFallbackCheck;
    FbCheck row;
    row.slotAbs = slotAbs;
    row.harqId = tx.harqId;
    row.gen = tx.gen;
    row.helperRnti = static_cast<uint16_t>(tx.helperRnti);
    row.helperNode = tx.helperNode;
    row.sbStart = sbStart;
    row.sbLen = sbLen;
    m_fbWatch[slotAbs].push_back(m_fbChecks.size());
    m_fbChecks.push_back(row);
    Simulator::Schedule(checkAt - Simulator::Now(),
                        &NackOnlyHarqController::CheckHelperRelay,
                        this,
                        idx,
                        slotAbs);
}

void
NackOnlyHarqController::OnLeaderRxSignal(uint32_t nodeId, uint8_t kind, Ptr<const SpectrumValue> psd)
{
    if (m_fbWatch.empty() || kind > 1 || !psd)
    {
        return;
    }
    const int64_t slotAbs =
        static_cast<int64_t>(std::floor(Simulator::Now().GetSeconds() / m_slot.GetSeconds() + 1e-9));
    auto w = m_fbWatch.find(slotAbs);
    if (w == m_fbWatch.end())
    {
        return;
    }
    const uint32_t rbPerSubch = m_mac->GetNrSlSubChSize();
    for (size_t r : w->second)
    {
        FbCheck& row = m_fbChecks[r];
        const bool isHelper = row.helperNode != 0 && nodeId == row.helperNode;
        const uint32_t rb0 = row.sbStart * rbPerSubch;
        const uint32_t rb1 = rb0 + row.sbLen * rbPerSubch;
        row.nRb = rb1 - rb0;
        double pw = 0.0;
        uint32_t rb = 0;
        auto band = psd->ConstBandsBegin();
        for (auto v = psd->ConstValuesBegin(); v != psd->ConstValuesEnd(); ++v, ++band, ++rb)
        {
            if (rb >= rb0 && rb < rb1)
            {
                pw += *v * (band->fh - band->fl);
            }
        }
        if (pw <= 0.0)
        {
            continue;
        }
        if (kind == 1)
        {
            (isHelper ? row.pDataHelperW : row.pDataOtherW) += pw;
            row.nOther += isHelper ? 0 : 1;
        }
        else
        {
            (isHelper ? row.pCtrlHelperW : row.pCtrlOtherW) += pw;
        }
    }
}

void
NackOnlyHarqController::CheckHelperRelay(size_t idx, int64_t slotAbs)
{
    const LeaderTx& tx = m_txs[idx];
    // the validation row of this check (registered by MaybeScheduleFallback)
    FbCheck* row = nullptr;
    auto w = m_fbWatch.find(slotAbs);
    if (w != m_fbWatch.end())
    {
        for (auto r = w->second.begin(); r != w->second.end(); ++r)
        {
            if (m_fbChecks[*r].gen == tx.gen && m_fbChecks[*r].harqId == tx.harqId)
            {
                row = &m_fbChecks[*r];
                w->second.erase(r);
                break;
            }
        }
        if (w->second.empty())
        {
            m_fbWatch.erase(w);
        }
    }
    if (row)
    {
        row->t = Simulator::Now().GetSeconds();
        auto f = m_sciFailed.find(static_cast<uint16_t>(tx.helperRnti));
        row->sciFailed = f != m_sciFailed.end() && f->second.count(slotAbs);
    }
    if (m_curGen[tx.harqId] != tx.gen)
    {
        if (row)
        {
            row->outcome = 3;
        }
        return; // the HARQ process already carries the next TB
    }
    auto it = m_sciHeard.find(static_cast<uint16_t>(tx.helperRnti));
    if (it != m_sciHeard.end() && it->second.count(slotAbs))
    {
        ++nFallbackHeard; // the helper relayed
        if (row)
        {
            row->outcome = 0;
        }
        return;
    }
    // nothing sensed from the helper where a relay was expected: it missed the TB
    if (ReclaimInTime(0, tx, nFallbackLate, nFallbackLateNoSlot))
    {
        ++nFallbackTx;
        m_pendingOrigin[tx.harqId] = 2;
        if (row)
        {
            row->outcome = 1;
        }
    }
    else
    {
        ++nFallbackNoSlot;
        if (row)
        {
            row->outcome = 2;
        }
    }
}


// ---------------------------------------------------------------------------
// Option 2 with a helper: the named helper never ACKs (receiver side)
// ---------------------------------------------------------------------------

namespace
{

class Option2HelperSilenceShim : public Object
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::Option2HelperSilenceShim").SetParent<Object>();
        return tid;
    }

    void Setup(Ptr<NrUePhy> phy,
               uint32_t nodeId,
               std::shared_ptr<uint64_t> suppressed,
               Callback<void, uint32_t, const SlHarqInfo&> observer)
    {
        m_phy = phy;
        m_nodeId = nodeId;
        m_suppressed = suppressed;
        m_observer = observer;
    }

    void OnFeedback(const SlHarqInfo& info)
    {
        // info.m_rnti is this receiver; m_helperRnti the helper the TB's SCI-2A names
        if (info.IsReceivedOk() && info.m_helperRnti != 0 && info.m_helperRnti == info.m_rnti)
        {
            ++*m_suppressed; // decoded as the named helper: stay silent, listen
            return;
        }
        if (!m_observer.IsNull())
        {
            m_observer(m_nodeId, info);
        }
        m_phy->EnqueueSlHarqFeedback(info);
    }

  private:
    Ptr<NrUePhy> m_phy;
    uint32_t m_nodeId{0};
    std::shared_ptr<uint64_t> m_suppressed;
    Callback<void, uint32_t, const SlHarqInfo&> m_observer;
};

} // namespace

std::shared_ptr<uint64_t>
InstallOption2HelperSilence(const std::vector<Ptr<NrUeNetDevice>>& devs,
                            Callback<void, uint32_t, const SlHarqInfo&> observer)
{
    auto suppressed = std::make_shared<uint64_t>(0);
    for (const auto& dev : devs)
    {
        for (uint8_t bwp = 0; bwp < dev->GetCcMapSize(); ++bwp)
        {
            Ptr<NrUePhy> phy = dev->GetPhy(bwp);
            if (!phy || !phy->GetSpectrumPhy())
            {
                continue;
            }
            Ptr<Option2HelperSilenceShim> shim = CreateObject<Option2HelperSilenceShim>();
            shim->Setup(phy, dev->GetNode()->GetId(), suppressed, observer);
            // the callback holds the Ptr: the shim lives as long as the binding
            phy->GetSpectrumPhy()->SetPhySlHarqFeedbackCallback(
                MakeCallback(&Option2HelperSilenceShim::OnFeedback, shim));
        }
    }
    return suppressed;
}

} // namespace ns3
