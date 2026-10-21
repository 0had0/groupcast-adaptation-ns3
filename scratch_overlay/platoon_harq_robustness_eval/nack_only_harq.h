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
    // Option 2 with a helper (PLAN.md R6): the helper named in this TB's SCI-2A never
    // ACKs; the leader ignores its silence and reacts only to its NACK
    uint32_t helperRnti{0};       //!< helper named for this TB (0 = none)
    uint32_t helperNode{0};       //!< its node index (0 = none)
    bool helperNack{false};       //!< the named helper NACKed this transmission
    bool helperReclaim{false};    //!< ... and the leader took back the next handed slot
    uint8_t origin{0};            //!< 0 regular; leader transmission in a handed slot taken
                                  //!< back: 1 on the helper's NACK, 2 by the fallback
                                  //!< (nothing sensed from the helper in its handed slot)
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
    // Option 2 with a helper named per TB (leader transmissions carrying a helper)
    uint64_t nHelperNamedTx{0};   //!< leader transmissions with a named helper
    uint64_t nHelperNack{0};      //!< NACKs of the named helper
    uint64_t nHelperReclaim{0};   //!< ... after which a handed-over slot was taken back
    uint64_t nHelperNackNoSlot{0};//!< ... with no handed-over slot left to take back
    uint64_t nHelperAck{0};       //!< ACKs of the named helper (must be 0: it stays silent)
    uint64_t nFbLate{0};          //!< feedback matching no pending PSFCH occasion (dropped)
    uint64_t nHelperNackStale{0}; //!< helper NACK for a TB its HARQ id no longer carries
    uint64_t nFallbackCheck{0};   //!< handed slots checked (a relay was expected)
    uint64_t nFallbackHeard{0};   //!< ... in which the leader decoded the helper's SCI
    uint64_t nFallbackTx{0};      //!< ... silent: the leader took back the next handed slot
    uint64_t nFallbackNoSlot{0};  //!< ... silent, but no handed slot was left

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

    /**
     * Bind each PSFCH only to the pending transmission whose PSFCH occasion it
     * is (it reaches the MAC half a symbol before that transmission's decision),
     * not to any pending transmission of the same HARQ id. Feedback that arrives
     * outside every occasion is counted in nFbLate and dropped, and a helper NACK
     * reclaims a handed-over slot only while the HARQ id still carries that TB.
     * Off: the earlier binding, under which a late helper NACK could reclaim a
     * slot of the next TB on the same HARQ id.
     */
    void SetExactPsfchAttribution(bool on) { m_exactPsfch = on; }

    /**
     * Option 1 with a named helper: the helper NACKs on a dedicated PSFCH resource
     * (not 3GPP: Option 1 shares one NACK resource, so a NACKer cannot be
     * identified), and the leader takes back the next handed-over slot on it, as
     * in Option 2. Members other than the helper stay on the shared resource.
     */
    void SetOpt1HelperNack(bool on) { m_opt1HelperNack = on && m_mode == HarqMode::OPT1; }

    /**
     * Helper takeover (R5, R6) off: the named helper is a plain member for the leader
     * (Option 2: it ACKs and counts in the all-ACK rule; no slot is taken back on its
     * NACK, no fallback). Arms R4.1 / R4.2. Call before the first transmission.
     */
    void SetHelperTakeover(bool on) { m_helperTakeover = on; }

    /**
     * Leader fallback (G08): after a NACK decision with a named helper that did not
     * NACK, the leader listens in the helper's next handed slot; if it decodes no SCI
     * of the helper there (the helper missed the TB), it takes back the following
     * handed slot and retransmits itself. Call after Install.
     */
    void SetLeaderFallback(bool on);

    // hooks used by the SAP wrappers
    void OnPsschPdu(size_t dev, Ptr<Packet> p, uint32_t dstL2Id);
    void OnPsfch(size_t dev, uint32_t nodeId, const SlHarqInfo& info);

  private:
    void Attach(Ptr<NrUeNetDevice> dev, uint32_t nodeIndex);
    void OnPsschScheduling(size_t dev, SlPsschUeMacStatParameters p);
    void Decide(size_t idx);
    void MaybeScheduleFallback(size_t idx);
    void CheckHelperRelay(size_t idx, int64_t slotAbs);
    void OnLeaderPscch(SlRxCtrlPacketTraceParams p);
    void DeliverSuccess(const LeaderTx& tx);

    Ptr<NrUeNetDevice> m_leader;
    Ptr<NrSlUeMac> m_mac;
    std::vector<Ptr<NrUeNetDevice>> m_members;
    uint32_t m_dst{254};
    HarqMode m_mode{HarqMode::OPT1};
    FeedbackCallback m_fbCallback;
    bool m_spsSafeAck{false};
    bool m_exactPsfch{false};
    bool m_opt1HelperNack{false};
    bool m_helperTakeover{true};
    bool m_fallback{false};
    std::map<uint8_t, uint8_t> m_pendingOrigin;           //!< HARQ id -> origin of its next retx
    std::map<uint16_t, std::set<int64_t>> m_sciHeard;     //!< leader: slots with a decoded SCI per RNTI
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
    std::map<size_t, uint32_t> m_pendingHelper; //!< dev -> helper in the SCI-2A just sent
};

/**
 * HARQ Option 2 with a helper named per TB (PLAN.md R6), receiver side: a member that
 * decoded a TB whose SCI-2A names it as the helper does not send its ACK (it listens to
 * the other members' PSFCH instead, half-duplex); its NACK is sent as usual. Re-binds the
 * PHY HARQ-feedback callback of every device (Option 2 installs no other gate).
 * @param devs      the UE devices (members)
 * @param observer  called with (node id, feedback) for every feedback actually sent
 * @return counter of suppressed helper ACKs (shared, read at the end of the run)
 */
std::shared_ptr<uint64_t> InstallOption2HelperSilence(
    const std::vector<Ptr<NrUeNetDevice>>& devs,
    Callback<void, uint32_t, const SlHarqInfo&> observer);

} // namespace ns3
