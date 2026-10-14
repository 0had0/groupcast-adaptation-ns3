// helper_select.h -- per-TB helper selection for the leader (Kuo et al., IEEE Access 2021)
//
// The leader picks, for every TB it publishes, the member that relays it in the handed-over
// retransmission slots, maximising the expected group PRR (Kuo eq. 17, one helper):
//   GPRR(i) = mean_r [ 1 - Pf(L->r) * ( Pf(L->i) + (1 - Pf(L->i)) * Pf(i->r) ) ]
// with Pf = 1 - P(success) of a link. P(success) follows Kuo eq. 7 with the TR 37.885 urban
// models the simulation uses: path loss LOS / NLOSv (LOS + blockage max{0, N(mu, 4.5 dB)},
// mu = 9 + max(0, 15 log10 d - 41)) / NLOS (building), shadowing, success if the path loss
// stays below the link budget PL_max of the TB's allocation at MCS 14.
//   kuo  distance only: P_LOS(d) = min(1, 1.05 exp(-0.0114 d)), else NLOSv (Kuo, no map)
//   map  building map + positions: a building on the segment -> NLOS, another vehicle's box
//        -> NLOSv, else LOS with probability P_LOS(d) (the simulator's own condition model)
//   static  always the fallback member (tests the selector path)
// Inputs are what a leader can know: member positions from CAM messages sampled every
// camPeriod, usable decisionDelay after they were sampled (processing), and the map. No
// random draw, no channel-model query: the selection cannot change any resource allocation.
#pragma once

#include "ns3/core-module.h"
#include "ns3/mobility-model.h"
#include "ns3/node-container.h"

#include "common/channel/three-gpp-geometric-nlosv.h"

#include <algorithm>
#include <deque>
#include <random>
#include <string>
#include <vector>

namespace ns3
{

constexpr double kPi = 3.14159265358979323846;

/// Named clamp enforcing mathematical probability bounds
inline double
ClampProbability(double p)
{
    if (!std::isfinite(p))
    {
        return 0.5;
    }
    return std::clamp(p, 1e-9, 1.0 - 1e-9);
}

inline double
ClampProb(double p)
{
    return ClampProbability(p);
}

enum class FeedbackKind : uint8_t
{
    ACK = 0,   //!< Successfully decoded TB
    NACK = 1,  //!< Overheard explicit Option-2 NACK
    DTX = 2    //!< Discontinuous Transmission (no PSFCH received, silent failure)
};

struct HelperSelectRow
{
    double tDecide{0.0};   //!< publication of the period's grant (s)
    double tNdi{0.0};      //!< slot of the TB's new transmission (s)
    double camAge{0.0};    //!< age of the positions used (s)
    uint8_t harqId{0};
    uint16_t nSubch{0};
    double plMaxDb{0.0};
    uint32_t chosen{0};    //!< member chosen by the policy (1..M)
    uint32_t applied{0};   //!< member signalled (= chosen, or the fallback in shadow mode)
    std::vector<float> score; //!< GPRR of every candidate (index member - 1), -1 = not a candidate
    uint32_t nLinkEval{0}; //!< link budget evaluations
    uint32_t nBldTest{0};  //!< building intersection tests
    double cpuUs{0.0};     //!< CPU execution time (microseconds)
};

class HelperSelector
{
  public:
    struct Config
    {
        std::string policy{"static"}; //!< static | kuo | map | harq | harqKuo | harqMap
        double camPeriodS{0.1};
        double decisionDelayS{0.0};
        uint32_t fallback{4};          //!< member used when no candidate is better / shadow
        bool shadow{false};            //!< compute and log, but signal the fallback
        double plMaxDb{0.0};           //!< 0 = from the link budget below
        double txPowerDbm{23.0};
        double noiseFigureDb{9.0};
        double sinrReqDb{8.0};         //!< MCS 14 decoding threshold (calibrated, see README)
        uint32_t subchPrbs{10};
        double fcGHz{5.89};
        double shadowLosDb{3.0};
        double shadowNlosDb{4.0};
        double posErrM{0.0};           //!< H3/map: position error std dev (m), 0 = exact
        double hstatWindowMs{1000.0};  //!< H4..H6: HARQ feedback history window (ms)
        double hstatPrior{10.0};       //!< H5/H6: Dirichlet prior n0 for model blending
        double pResource{1.0};         //!< Kuo 2021 faithful reproduction: pResource = 1.0
        std::string scenario{"urban"}; //!< urban | highway
        uint64_t prngSeed{1234567};    //!< seed for position error PRNG (deterministic)
    };

    void Init(const Config& cfg,
              const NodeContainer& nodes,
              uint32_t numMembers,
              Ptr<ThreeGppGeometricNlosvModel> geom);

    /// Record member HARQ feedback for H4..H6 (memberIndex is 1..M, one record per TB trial)
    void OnFeedback(double tS, uint32_t memberIndex, uint32_t tbGen, uint8_t kind);
    void OnFeedbackSimple(double tS, uint32_t memberIndex, bool ack)
    {
        OnFeedback(tS, memberIndex, 0, ack ? 0 : 1);
    }

    /// member (1..M) to signal for the TB published now
    uint32_t Choose(double now, double tNdi, uint8_t harqId, uint16_t nSubch);

    const std::vector<HelperSelectRow>& Rows() const { return m_rows; }
    const Config& Cfg() const { return m_cfg; }
    double PlMaxDb(uint16_t nSubch) const;

  private:
    struct Cam
    {
        double t;
        std::vector<Vector> pos;   //!< leader + members (index 0..M)
        std::vector<Vector> bgPos; //!< background vehicles (blockers)
    };
    struct FeedbackRecord
    {
        double t;
        uint32_t member;   //!< 1..M
        uint32_t tbGen;    //!< TB generation ID
        FeedbackKind kind; //!< ACK, NACK, DTX
    };
    void Snapshot();
    double PSuccess(const Cam& c, uint32_t ia, uint32_t ib, double plMax, uint32_t& linkCount, uint32_t& bldCount) const;
    double PlLos(double d) const;
    double PlNlos(double d) const;
    int Geometry(const Cam& c, uint32_t ia, uint32_t ib, uint32_t& bldCount) const; //!< 0 building, 1 vehicle, 2 LOS

    Config m_cfg;
    NodeContainer m_nodes;
    uint32_t m_numMembers{8};
    Ptr<ThreeGppGeometricNlosvModel> m_geom;
    std::deque<Cam> m_cams;
    std::deque<FeedbackRecord> m_feedback;
    std::vector<HelperSelectRow> m_rows;
    std::mt19937_64 m_posRng;
};

} // namespace ns3
