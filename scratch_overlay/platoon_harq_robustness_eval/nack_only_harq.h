#pragma once
// nack_only_harq.h
//
// TX-side HARQ feedback Option 1 (groupcast NACK-only) for the platoon leader,
// plus exact application-packet -> transport-block bookkeeping.
//
// WHY
//   contrib/nr pre-selects up to sl-MaxTxTransNumPSSCH resources for every TB
//   (initial + retransmissions, spaced so that the PSFCH of one transmission
//   can be processed before the next one) and only *cancels* the pending
//   retransmissions when the HARQ entity receives a positive acknowledgement.
//   That is Option-2 (ACK/NACK) semantics.
//   With Option 1 the receivers never send an ACK (see
//   common/mcd/mcr_nack_only_gate.h, which drops the ACKs at the receivers),
//   so without this controller the leader would always transmit every TB
//   maxNumTx times, i.e. blind retransmission.
//
//   TS 38.321 5.22.1.3.1a: for a groupcast with HARQ feedback option 1 the
//   transmitter treats "no PSFCH received" as a positive acknowledgement.
//   This controller implements exactly that decision:
//     * every leader PSSCH transmission in slot n is mapped to its PSFCH
//       occasion m = first PSFCH slot >= n + sl-MinTimeGapPSFCH;
//     * the members' NACKs reach the leader MAC at m + 13 symbols + 1 slot
//       (NrUePhy::PhyPsfchReceived adds one slot of decode latency);
//     * half a symbol later the controller decides: if no NACK arrived for
//       this transmission, it delivers the implicit positive acknowledgement
//       to the MAC (one ACK per member: stock CTTC cancels on the first one,
//       an "all members must ACK" variant of NrSlUeMacHarq on the last one),
//       which cancels the remaining reserved retransmissions of the TB;
//       if at least one NACK arrived nothing is done and the next reserved
//       retransmission goes out (NACK-triggered retransmission).
//   sl-MinTimeGapProcessing (2 slots) guarantees that the next retransmission
//   resource is never earlier than m + 2, i.e. after the decision instant.
//
// HOW (no edit of contrib/nr)
//   The leader's PHY->MAC SAP user and MAC->PHY SAP provider are wrapped:
//     PHY->MAC  ReceivePsfch      : NACKs are recorded, then forwarded
//     MAC->PHY  SendPsschMacPdu   : the PlatoonSeqTag byte tags carried by the
//                                   MAC PDU tell which application packets
//                                   ride in this TB (exact, survives RLC
//                                   segmentation/concatenation and HARQ
//                                   retransmissions)
//   and the leader MAC "SlPsschScheduling" trace closes each transmission.

#include "ns3/nr-sl-phy-mac-common.h"
#include "ns3/nr-sl-ue-mac.h"
#include "ns3/nr-sl-ue-phy-sap.h"
#include "ns3/nr-ue-net-device.h"
#include "ns3/packet.h"
#include "ns3/tag.h"

#include <cstdint>
#include <map>
#include <memory>
#include <set>
#include <vector>

namespace ns3
{

/// Byte tag carrying the application sequence number of a leader packet.
class PlatoonSeqTag : public Tag
{
  public:
    static TypeId GetTypeId();
    TypeId GetInstanceTypeId() const override;
    uint32_t GetSerializedSize() const override;
    void Serialize(TagBuffer i) const override;
    void Deserialize(TagBuffer i) override;
    void Print(std::ostream& os) const override;

    void SetSeq(uint32_t s) { m_seq = s; }
    uint32_t GetSeq() const { return m_seq; }

  private:
    uint32_t m_seq{0};
};

/// One PSSCH transmission of the leader (initial or retransmission).
struct LeaderTx
{
    double tS{0.0};               //!< slot start (s)
    int64_t slotAbs{0};           //!< absolute slot index = round(t / Tslot)
    uint16_t frame{0};
    uint8_t subframe{0};
    uint16_t slot{0};
    uint16_t rbStart{0};
    uint16_t rbLen{0};
    uint8_t harqId{0};
    uint8_t ndi{0};
    uint8_t rv{0};
    uint32_t gen{0};              //!< index of the ndi=1 transmission of this TB
    uint8_t txInTb{1};            //!< 1 = initial, 2 = first retx, ...
    std::vector<uint32_t> seqs;   //!< application packets carried
    uint8_t nNack{0};             //!< NACKs received for this transmission
    std::set<uint32_t> ackedMembers;  //!< member nodeIds that sent ACK for this transmission
    std::set<uint32_t> nackedMembers; //!< member nodeIds that sent NACK for this transmission
    int8_t decision{-1};          //!< -1 pending, 0 NACK (retx kept), 1 implicit/all ACK, 2 stale
    double decideAtS{0.0};        //!< time of the decision (its PSFCH occasion + 1 slot + 13.5 sym)
    uint32_t node{0};             //!< transmitting node (index in the node container)
    uint16_t rnti{0};             //!< transmitting UE's RNTI
};

class NackOnlyHarqController
{
  public:
    enum class HarqMode : uint8_t
    {
        BLIND = 0,
        OPT1 = 1,
        OPT2 = 2
    };

    /**
     * @param leader      leader device (node 0)
     * @param members     group members (receivers)
     * @param dstL2Id     groupcast destination of the platoon flow
     * @param mode        HARQ mode: blind, opt1 (NACK-only), or opt2 (all-ACK)
     * @param minTimeGapPsfch sl-MinTimeGapPSFCH configured in the pool (slots)
     */
    void Install(Ptr<NrUeNetDevice> leader,
                 const std::vector<Ptr<NrUeNetDevice>>& members,
                 uint32_t dstL2Id,
                 HarqMode mode,
                 uint8_t minTimeGapPsfch);

    void Install(Ptr<NrUeNetDevice> leader,
                 const std::vector<Ptr<NrUeNetDevice>>& members,
                 uint32_t dstL2Id,
                 bool option1Tx,
                 uint8_t minTimeGapPsfch);

    /**
     * Observe-only (option1Tx false) controllers: also record the platoon-destination
     * transmissions of another device (e.g. every member that may relay as helper);
     * each record carries its transmitting node and RNTI.
     * @param dev       the device
     * @param nodeIndex the device's node index (stored in LeaderTx::node)
     */
    void AddObservedDevice(Ptr<NrUeNetDevice> dev, uint32_t nodeIndex);

    /// Application TagDecorator: stamps the next sequence number.
    void TagAppPacket(Ptr<Packet> p);

    const std::vector<LeaderTx>& Txs() const { return m_txs; }
    Time SlotPeriod() const { return m_slot; }

    // statistics
    uint64_t nNackRx{0};          //!< NACK PSFCH received by the leader
    uint64_t nAckRx{0};           //!< explicit ACKs received (0 with Option 1)
    uint64_t nImplicitAck{0};     //!< decisions "no NACK -> success"
    uint64_t nAllAckDecision{0};  //!< Option 2 decisions "all members ACKed -> success"
    uint64_t nNackDecision{0};    //!< decisions "NACK -> keep retransmission"
    uint64_t nStale{0};           //!< decisions skipped (HARQ id already reused)
    uint64_t nSlotMisaligned{0};  //!< MAC trace not at a slot boundary (sanity)
    uint64_t nAckNoRetxLeft{0};   //!< SPS: implicit ACK not delivered, no reserved retx left

    HarqMode GetMode() const { return m_mode; }
    typedef Callback<void, double, uint32_t, uint32_t, uint8_t> FeedbackCallback;
    void SetFeedbackCallback(FeedbackCallback cb) { m_fbCallback = cb; }

    /**
     * SPS leader: deliver an implicit ACK only while the TB's reserved
     * retransmissions of the current period remain. After the last of them the
     * ACK has nothing to cancel, and the HARQ process (kept across periods) may
     * already hold the next period's TB: the MAC would erase that grant and the
     * HARQ entity flush that TB.
     */
    void SetSpsSafeAck(bool on) { m_spsSafeAck = on; }

    // hooks used by the SAP wrappers
    void OnPsschPdu(size_t dev, Ptr<Packet> p, uint32_t dstL2Id);
    void OnPsfch(size_t dev, uint32_t nodeId, const SlHarqInfo& info);

  private:
    void Attach(Ptr<NrUeNetDevice> dev, uint32_t nodeIndex);
    void OnPsschScheduling(size_t dev, SlPsschUeMacStatParameters p);
    void Decide(size_t idx);
    void DeliverSuccess(const LeaderTx& tx);

    Ptr<NrUeNetDevice> m_leader;
    Ptr<NrSlUeMac> m_mac;
    std::vector<Ptr<NrUeNetDevice>> m_members;
    uint32_t m_dst{254};
    HarqMode m_mode{HarqMode::OPT1};
    FeedbackCallback m_fbCallback;
    bool m_spsSafeAck{false};
    uint8_t m_gap{3};
    uint8_t m_mu{1};
    Time m_slot;
    Time m_sym;
    uint32_t m_nextSeq{0};

    NrSlUePhySapUser* m_origUser{nullptr}; //!< first device's (ACK injection)
    // per observed device (index 0 = the device given to Install)
    std::vector<Ptr<NrSlUeMac>> m_macs;
    std::vector<uint32_t> m_devNode;
    std::vector<std::unique_ptr<NrSlUePhySapUser>> m_userWraps;
    std::vector<std::unique_ptr<NrSlUePhySapProvider>> m_provWraps;
    std::vector<std::set<uint32_t>> m_pendingSeqs;
    std::vector<LeaderTx> m_txs;
    std::map<uint8_t, uint32_t> m_curGen;      //!< harqId -> current TB generation
    std::map<uint8_t, size_t> m_lastTxOfHarq;  //!< harqId -> index of its last transmission
};

} // namespace ns3
