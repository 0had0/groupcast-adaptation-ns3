// micro_sl.h -- in-process stock-NR sidelink groupcast scenario (1 TX + N RX) with an event log, after
// contrib/nr/examples/sl-examples/sl-harq-example.cc.  Used by A02 (PSFCH timing), B03 (stock first-ACK-cancels,
// DV2/F12) and B04 (feedback binding, DV4/F29).  No study wrappers are installed: this is what the NR module does
// by itself.  Deterministic always-LOS channel (RMa_LoS, no shadowing), fixed MCS 14.
#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace ptest
{

struct MicroCfg
{
    std::vector<double> rxDistM{20.0, 40.0, 60.0}; //!< RX k at distance d_k from the TX on separate bearings
    std::string cast{"groupcast"};                 //!< groupcast (3GPP Option 2) | nackonly (Option 1) | broadcast
    bool harq{true};
    uint16_t psfchPeriod{1};
    uint8_t maxNumTx{3};
    uint8_t maxNumPerReserve{3};
    bool dedicated{true};   //!< every slot is a sidelink slot (all-UL TDD pattern, all-ones bitmap), as the study
    bool dynamic{true};     //!< dynamic grants (else SPS with rri)
    double rriMs{100.0};
    uint32_t nPackets{100};
    double firstPacketS{2.5};
    double intervalMs{40.0};
    uint32_t pktBytes{300};
    double pdbMs{50.0};
    uint32_t seed{1};
    uint32_t mcs{14};
    double extraSimS{0.5};
    uint32_t mu{1};
    double txPowerDbm{23.0};
    /// test-only: deliver a synthetic PSFCH feedback to the TX MAC at time t (as if RX rx had sent it)
    struct Inject
    {
        double t;
        int harq;
        bool ack;
        int rx;
    };
    std::vector<Inject> inject;
    /// optional: called for each feedback an RX is about to enqueue; return false to drop it (test-only PSFCH loss)
    std::function<bool(int rx, int harq, bool ack)> feedbackFilter;
};

struct MicroLog
{
    struct PsschTx
    {
        double t;
        int64_t slot;
        int harq, ndi, rv, rbStart, rbLen;
    };
    struct PsschRx
    {
        double t;
        int rx;
        int64_t slot;
        int ndi, rv;
        bool corrupt, sci2Corrupt;
        double sinr;
    };
    struct FbQueued //!< decided at the RX PHY (feedback callback), before the PSFCH slot
    {
        double t;
        int rx;
        int harq;
        bool ack;
        int txRnti, rxRnti;
    };
    struct FbTx //!< PSFCH transmission by an RX (TxFeedbackTrace)
    {
        double t;
        int rx;
        int64_t slot;
    };
    struct FbRx //!< feedback delivered to the TX MAC (NrSlUeMacHarq::RxHarqFeedback)
    {
        double t;
        int harq;
        bool ack;
        int rxRnti;
    };
    struct Timeout
    {
        double t;
        int harq;
    };
    std::vector<PsschTx> tx;
    std::vector<PsschRx> rx;
    std::vector<FbQueued> fbQueued;
    std::vector<FbTx> fbTx;
    std::vector<FbRx> fbRx;
    std::vector<Timeout> timeouts;
    std::vector<std::vector<uint32_t>> appRx; //!< per RX: received sequence numbers
    uint32_t appSent{0};
    // pool parameters read from the TX MAC
    int psfchPeriodPool{-1};
    int minGapPsfch{-1};
    std::vector<int> txRnti; //!< RNTI of node 0 at index 0, then of each RX
    std::vector<uint8_t> psfchSlotMask; //!< SlotHasPsfch(slot) for slots 0..N-1 (N = psfchMaskSlots)
    int psfchMaskSlots{0};
    int slotsPerSubframe{2};
};

struct FailDistance
{
    bool found{false};
    double distM{0};
    double corruptFrac{0}, sci2Frac{0};
    long events{0};
    std::string table; //!< "d: corrupt/sci2/n" for every probed distance
};
/// Smallest distance (probed set) at which a TX -> RX PSSCH (MCS 14) is corrupt in >= 90% of the receptions (retransmissions soft-combine, so
/// 100% is not reachable) while the SCI is decoded in >= 90%: a receiver that is "always failing" but still sends NACKs.
FailDistance FindFailingDistance();

/// Build and run the scenario; fills the log. Returns false on setup error (message in *err).
bool RunMicro(const MicroCfg& cfg, MicroLog& log, std::string* err = nullptr);

} // namespace ptest
