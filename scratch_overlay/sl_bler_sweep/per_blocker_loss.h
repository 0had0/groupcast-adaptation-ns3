#pragma once
// per_blocker_loss.h
//
// Custom additive NLOSv blockage loss that is a FUNCTION OF THE NUMBER OF
// BLOCKERS between TX and RX.
//
// Why this exists: the stock 3GPP TR 37.885 V2V model (and the geometric
// channel-condition wrapper used here) treat NLOSv as a single binary state.
// GetAdditionalNlosvLoss(a,b) in
//   src/propagation/model/three-gpp-v2v-propagation-loss-model.cc
// draws ONE log-normal blockage loss from the endpoint distance + one randomly
// chosen blocker height; it never sees how many vehicles actually obstruct the
// path. So 1 vs 3 blockers would give statistically identical NLOSv loss.
//
// This PropagationLossModel is chained (SetNext) AFTER the 3GPP propagation loss
// model. It counts how many blocker footprints intersect the TX->RX segment and
// adds one independent log-normal blockage term per *extra* blocker (count-1),
// because the geometric NLOSv condition already contributes the first blocker's
// loss. Result: N=1 matches stock NLOSv, N=2/3 attenuate progressively more.
//
// This file lives entirely in scratch_overlay/sl_bler_sweep/ — no ns-3 edits.

#include "ns3/node-container.h"
#include "ns3/propagation-loss-model.h"
#include "ns3/random-variable-stream.h"

namespace ns3
{

class MobilityModel;

class PerBlockerNlosvLoss : public PropagationLossModel
{
  public:
    static TypeId GetTypeId();
    PerBlockerNlosvLoss();
    ~PerBlockerNlosvLoss() override;

    // Nodes whose MobilityModel positions act as blocker centroids.
    void SetBlockerNodes(const NodeContainer& nodes);
    // Physical footprint of each blocker (metres). Stored as half-extents.
    void SetBlockerDimensions(double lengthM, double widthM);

  private:
    double DoCalcRxPower(double txPowerDbm,
                         Ptr<MobilityModel> a,
                         Ptr<MobilityModel> b) const override;
    int64_t DoAssignStreams(int64_t stream) override;

    // Liang–Barsky segment-vs-AABB: true iff (x1,y1)->(x2,y2) crosses the
    // rectangle centred at (cx,cy) with half-extents (hl,hw).
    static bool SegmentIntersectsAabb(double x1, double y1,
                                      double x2, double y2,
                                      double cx, double cy,
                                      double hl, double hw);

    NodeContainer m_blockers;
    double        m_halfLen{2.25};   // 4.5 m / 2
    double        m_halfWidth{1.0};  // 2.0 m / 2
    Ptr<LogNormalRandomVariable> m_logNor;
};

} // namespace ns3
