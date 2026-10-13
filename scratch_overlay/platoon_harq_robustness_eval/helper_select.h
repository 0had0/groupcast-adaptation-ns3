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

#include <deque>
#include <string>
#include <vector>

namespace ns3
{

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
};

class HelperSelector
{
  public:
    struct Config
    {
        std::string policy{"static"}; //!< static | kuo | map
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
    };

    void Init(const Config& cfg,
              const NodeContainer& nodes,
              uint32_t numMembers,
              Ptr<ThreeGppGeometricNlosvModel> geom);

    /// member (1..M) to signal for the TB published now
    uint32_t Choose(double now, double tNdi, uint8_t harqId, uint16_t nSubch);

    const std::vector<HelperSelectRow>& Rows() const { return m_rows; }
    const Config& Cfg() const { return m_cfg; }
    double PlMaxDb(uint16_t nSubch) const;

  private:
    struct Cam
    {
        double t;
        std::vector<Vector> pos; //!< leader + members
    };
    void Snapshot();
    double PSuccess(const Cam& c, uint32_t ia, uint32_t ib, double plMax) const;
    double PlLos(double d) const;
    double PlNlos(double d) const;
    int Geometry(const Cam& c, uint32_t ia, uint32_t ib) const; //!< 0 building, 1 vehicle, 2 LOS

    Config m_cfg;
    NodeContainer m_nodes;
    uint32_t m_numMembers{8};
    Ptr<ThreeGppGeometricNlosvModel> m_geom;
    std::deque<Cam> m_cams;
    std::vector<HelperSelectRow> m_rows;
};

} // namespace ns3
