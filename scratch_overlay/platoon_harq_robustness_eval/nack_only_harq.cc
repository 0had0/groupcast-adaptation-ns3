// nack_only_harq.cc -- see nack_only_harq.h

#include "nack_only_harq.h"

#include "ns3/log.h"
#include "ns3/node.h"
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
    UserWrap(NrSlUePhySapUser* orig, NackOnlyHarqController* ctl)
        : m_orig(orig),
          m_ctl(ctl)
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
        m_ctl->OnPsfch(sendingNodeId, harqInfo);
        m_orig->ReceivePsfch(sendingNodeId, harqInfo);
    }

  private:
    NrSlUePhySapUser* m_orig;
    NackOnlyHarqController* m_ctl;
};

class ProvWrap : public NrSlUePhySapProvider
{
  public:
    ProvWrap(NrSlUePhySapProvider* orig, NackOnlyHarqController* ctl)
        : m_orig(orig),
          m_ctl(ctl)
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
        m_ctl->OnPsschPdu(p, dstL2Id);
        m_orig->SendPsschMacPdu(p, dstL2Id);
    }

    void SetNrSlVarTtiAllocInfo(const SfnSf& sfn, const NrSlVarTtiAllocInfo& varTtiInfo) override
    {
        m_orig->SetNrSlVarTtiAllocInfo(sfn, varTtiInfo);
    }

  private:
    NrSlUePhySapProvider* m_orig;
    NackOnlyHarqController* m_ctl;
};

} // namespace

// ---------------------------------------------------------------------------
// NackOnlyHarqController
// ---------------------------------------------------------------------------

void
NackOnlyHarqController::Install(Ptr<NrUeNetDevice> leader,
                                const std::vector<Ptr<NrUeNetDevice>>& members,
                                uint32_t dstL2Id,
                                bool option1Tx,
                                uint8_t minTimeGapPsfch)
{
    m_leader = leader;
    m_members = members;
    m_dst = dstL2Id;
    m_option1 = option1Tx;
    m_gap = minTimeGapPsfch;

    m_mac = leader->GetMac(0)->GetObject<NrSlUeMac>();
    NS_ABORT_MSG_IF(!m_mac, "leader has no NrSlUeMac");
    Ptr<NrUePhy> phy = leader->GetPhy(0);
    m_slot = phy->GetSlotPeriod();
    m_sym = phy->GetSymbolPeriod();
    m_mu = static_cast<uint8_t>(std::lround(std::log2(1e-3 / m_slot.GetSeconds())));

    m_origUser = m_mac->GetNrSlUePhySapUser();
    m_origProv = phy->GetNrSlUePhySapProvider();
    m_userWrap = std::make_unique<UserWrap>(m_origUser, this);
    m_provWrap = std::make_unique<ProvWrap>(m_origProv, this);
    phy->SetNrSlUePhySapUser(m_userWrap.get());
    m_mac->SetNrSlUePhySapProvider(m_provWrap.get());

    m_mac->TraceConnectWithoutContext(
        "SlPsschScheduling",
        MakeCallback(&NackOnlyHarqController::OnPsschScheduling, this));
}

void
NackOnlyHarqController::TagAppPacket(Ptr<Packet> p)
{
    PlatoonSeqTag tag;
    tag.SetSeq(m_nextSeq++);
    p->AddByteTag(tag);
}

void
NackOnlyHarqController::OnPsschPdu(Ptr<Packet> p, uint32_t dstL2Id)
{
    if (dstL2Id != m_dst)
    {
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
            m_pendingSeqs.insert(tag.GetSeq());
        }
    }
}

void
NackOnlyHarqController::OnPsschScheduling(SlPsschUeMacStatParameters p)
{
    if (p.dstL2Id != m_dst)
    {
        m_pendingSeqs.clear();
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
    tx.seqs.assign(m_pendingSeqs.begin(), m_pendingSeqs.end());
    m_pendingSeqs.clear();

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
    // NACKs reach the MAC at  t(m) + 13 sym (end of PSFCH) + 1 slot;
    // decide half a symbol later, before slot m + 2 starts.
    const Time decideAt = m_slot * static_cast<int64_t>(k + 1) + m_sym * 13 + m_sym / 2;
    m_txs[idx].decideAtS = (Simulator::Now() + decideAt).GetSeconds();
    Simulator::Schedule(decideAt, &NackOnlyHarqController::Decide, this, idx);
}

void
NackOnlyHarqController::OnPsfch(uint32_t nodeId, const SlHarqInfo& info)
{
    if (info.m_dstL2Id != m_dst || info.m_txRnti != m_mac->GetRnti())
    {
        return;
    }
    if (info.IsReceivedOk())
    {
        ++nAckRx;
        return;
    }
    ++nNackRx;
    // attribute the NACK to the pending transmission of this HARQ id whose
    // PSFCH occasion this is: the one decided next. A PSFCH carries no HARQ id
    // (TS 38.213 sec. 16.3: the occasion and PRB identify the PSSCH); with SPS
    // the HARQ id is the same in every period, so the latest pending
    // transmission can be the next period's TB while this NACK is for the last
    // retransmission of the previous one. With one pending transmission per
    // HARQ id (dynamic grants) both rules pick the same one.
    const double now = Simulator::Now().GetSeconds();
    LeaderTx* best = nullptr;
    for (size_t i = m_txs.size(); i-- > 0;)
    {
        LeaderTx& tx = m_txs[i];
        if (tx.harqId == info.m_harqProcessId && tx.decision < 0 && tx.tS < now &&
            (!best || tx.decideAtS < best->decideAtS))
        {
            best = &tx;
        }
        if (now - tx.tS > 0.1)
        {
            break; // older than any pending decision
        }
    }
    if (best && best->nNack < 255)
    {
        ++best->nNack;
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
    if (tx.nNack > 0)
    {
        tx.decision = 0;
        ++nNackDecision;
        return;
    }
    tx.decision = 1;
    ++nImplicitAck;
    if (!m_option1)
    {
        return;
    }
    if (m_spsSafeAck && !m_mac->IsSlRetxPending(m_dst, tx.harqId))
    {
        ++nAckNoRetxLeft;
        return;
    }
    // Option 1: no NACK => positive acknowledgement. Deliver it through the
    // original SAP so NrSlUeMac::DoReceivePsfch cancels the remaining
    // reserved retransmissions of this TB (one ACK per member, so it also
    // works with an "all members must ACK" variant of RecvHarqFeedback).
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

} // namespace ns3
