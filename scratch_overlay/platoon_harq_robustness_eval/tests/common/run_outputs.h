// run_outputs.h -- load the CSV outputs of one simulation run and reconstruct the platoon transmissions, the
// transport blocks (TB) and the per-member decode sequences (TEST_PLAN sections 6.2 and 8 "Common TB reconstruction").
//
// A TB = the leader transmissions from a ndi=1 row on (same harq_id, tx_in_tb 2,3,...) plus the helper copies the
// eval attributes to it (_att column 'tb' = row index of the TB's ndi=1 leader transmission = LeaderTx::gen).
#pragma once

#include "common/csv.h"
#include "common/runsets.h"

#include <array>
#include <cstdint>
#include <map>
#include <string>
#include <unordered_map>
#include <vector>

namespace ptest
{

constexpr int kMembers = 8;
constexpr double kSlotS = 0.0005; //!< mu = 1
constexpr int kSubchPrbs = 10;

/// One platoon PSSCH transmission (leader, or a helper copy of a leader TB).
struct PTx
{
    int side{0};         //!< 0 leader, 1 helper copy
    int row{-1};         //!< row in _ltx / _htx
    double t{0};         //!< slot start (s)
    int64_t slot{0};     //!< slot_abs
    int rbStart{0}, rbLen{0}, harq{0}, ndi{0}, rv{0}, txInTb{0}, nSeq{0}, nNack{0}, decision{-1}, nColl{0},
        nNackSent{0};
    double nearestColl{-1};
    int64_t tb{-1};      //!< leader TB id (row of the ndi=1 transmission); -1 if unknown
    int node{-1};        //!< transmitting node (0 = leader)
    bool hasAtt{false};
    uint16_t nackMask{0};   //!< members whose NACK is attributed to this transmission (bit m-1), from _nack.csv
    std::array<int8_t, kMembers> status{};  //!< 0 no decode attempt (SCI missed), 1 ok, 2 corrupt
    std::array<float, kMembers> sinr{};
    std::array<int8_t, kMembers> cond{};
    std::array<float, kMembers> dist{};
    double subch() const { return static_cast<double>(rbLen) / kSubchPrbs; }
};

struct Tb
{
    int64_t id{-1};
    std::vector<int> tx;      //!< indices into Run::txs, time ordered (leader and helper)
    int harq{-1};
    double tGen{NAN};         //!< generation time of the (first) packet it carries, NaN if outside the KPI window
    int64_t firstSeq{-1};
};

struct NackRow
{
    double t;
    int member, rnti, harq;
};

class Run
{
  public:
    RunRef ref;
    Table runT, memberT, pktT, ltxT, htxT, nackT, attT, alltxT, grantsT, hselT;
    bool hasLtx{false}, hasHtx{false}, hasAtt{false}, hasAlltx{false}, hasGrants{false}, hasHsel{false};
    double kpiStart{0}, kpiEnd{0}, pdbS{0.1};
    int maxNumTx{3};
    std::string harqMode;     //!< from run.csv harq_mode
    std::string traffic;
    std::vector<PTx> txs;     //!< time ordered
    std::map<int64_t, Tb> tbs;
    std::vector<NackRow> nacks;
    int helperNodeStatic{0};  //!< run.csv helper
    std::vector<int> nackTx;  //!< per _nack.csv row: matched tx index in txs (latest tx of that RNTI + HARQ id decoded inside its slot), -1 unmatched
    std::vector<bool> nackAmb; //!< ... and whether a second TB of that HARQ id lies within 32 slots
    long nackUnmatched{0}, nackAmbiguous{0};

    /// Load everything that exists. Returns false if run.csv is missing.
    bool Load(const RunRef& r, std::string* err = nullptr);

    bool InWin(double t) const { return t >= kpiStart && t < kpiEnd; }
    double R(const std::string& col, double def = NAN) const { return runT.R0(col, def); }
    std::string Rs(const std::string& col) const { return runT.R0s(col); }

    /// per-member prr / prr_pdb (member 1..8) from _member.csv
    double MemberCol(const std::string& col, int member) const;
    /// first TB index (in tbs order) of the transmissions, the leader TBs whose first transmission is in the KPI window
    std::vector<int64_t> WindowTbs() const;
    /// number of leader TBs started (ndi=1 rows) whose t in the KPI window
    long NewTbsInWindow() const;
    /// slots_or_counter of the last leader SPS grant created at or before t (-1: none / no _grants.csv)
    int GrantSlotsAt(double t) const;
    /// leader SPS grants created: (all, with fewer than maxNumTx slots, with a single slot)
    void GrantSlotStats(long& all, long& lessThanMax, long& single) const;
    /// Cumulative decode: has member m (1..8) decoded TB tb by transmission position k (inclusive) in tbs[tb].tx?
    bool DecodedBy(const Tb& tb, int k, int member) const;
    /// is the member the transmitter of that tx (helper copies are not attempts for the helper itself)
    bool IsSelf(const PTx& x, int member) const { return x.side == 1 && x.node == member; }
    /// transmitting node -> RNTI (rnti = node + 1 in this simulator; verified against _nack.csv)
    static int RntiOfNode(int node) { return node + 1; }

    /// helper named for the TB whose leader transmission is `initial` (_hsel applied at t_ndi); static --helper if no row
    int HelperOf(const PTx& initial) const;
    /// effective HARQ id of a transmission as carried in the SCI-2A: a helper copy carries the leader TB's HARQ id
    int EffHarq(const PTx& x) const;
    /// ptx index of the leader/helper tx in slot (side, slot), -1 if none
    int FindTx(int side, int64_t slot) const;

    /// pkt.csv column of member m: vector in packet order (rows of that member)
    struct PktRow
    {
        int64_t seq;
        double tTx;
        int size;
        bool rx;
        double delayMs;
        int cause, nAtt, nAttColl, nAttFail;
        double distM;
        int cond;
        double bestSinr;
    };
    /// rows per member [0..7] (packet order)
    std::array<std::vector<PktRow>, kMembers> pkts;

  private:
    void BuildTxs();
    void BuildTbs();
    void LoadPkts();
    void AttributeNacks();
    std::unordered_map<int64_t, int> m_slotIdx[2];
    std::unordered_map<int64_t, int> m_hselBySlot; //!< slot of t_ndi -> applied member
};

/// A TB whose leader initial transmission lies in the KPI window, with its transmissions split by side.
struct TbView
{
    int64_t id{-1};
    const Tb* tb{nullptr};
    const PTx* initial{nullptr};
    std::vector<const PTx*> all;     //!< time ordered, leader and helper
    std::vector<const PTx*> leader;
    std::vector<const PTx*> helper;
    int helperNode{-1};              //!< named helper (hsel applied, else the static --helper); -1 without helper
    double tGen{NAN};
};
std::vector<TbView> WindowTbViews(const Run& r);

} // namespace ptest
