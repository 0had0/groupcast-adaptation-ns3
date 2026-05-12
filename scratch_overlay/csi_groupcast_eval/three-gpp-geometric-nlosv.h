/*
 * Geometric NLOSv channel-condition wrapper.
 *
 * Decorates any ChannelConditionModel and deterministically forces NLOSv
 * whenever a designated SUMO blocker vehicle's 2-D footprint intersects the
 * straight-line path between TX and RX.  Without blockers configured the
 * wrapper is fully transparent.
 *
 * Intersection geometry uses the Liang–Barsky algorithm against an
 * axis-aligned bounding box (AABB) centred on each blocker; the AABB is
 * the blocker's (length × width) footprint.  This is strictly more correct
 * than the previous bounding-box overlap heuristic.
 */

#ifndef THREE_GPP_GEOMETRIC_NLOSV_H
#define THREE_GPP_GEOMETRIC_NLOSV_H

#include "ns3/channel-condition-model.h"
#include "ns3/mobility-model.h"
#include "ns3/node-container.h"
#include "ns3/object.h"
#include "ns3/ptr.h"
#include "ns3/traci-client.h"
#include "ns3/vector.h"

#include <string>
#include <vector>

namespace ns3
{

class ThreeGppGeometricNlosvModel : public ChannelConditionModel
{
  public:
    static TypeId GetTypeId();

    ThreeGppGeometricNlosvModel();
    ~ThreeGppGeometricNlosvModel() override;

    // ChannelConditionModel interface
    Ptr<ChannelCondition> GetChannelCondition(Ptr<const MobilityModel> a,
                                              Ptr<const MobilityModel> b) const override;
    int64_t AssignStreams(int64_t stream) override;

    void SetWrappedModel(Ptr<ChannelConditionModel> model);
    void SetTraciClient(Ptr<TraciClient> traci);

    // IDs of SUMO vehicles treated as blocking obstacles (e.g. trucks).
    void SetBlockerVehicleIds(const std::vector<std::string>& ids);

    // Alternative to TraCI: directly supply the NS-3 nodes whose MobilityModel
    // positions are used as blocker centroids.  Takes precedence over TraCI when set.
    void SetBlockerNodes(const NodeContainer& nodes);

    // Physical dimensions of each blocker.
    void SetBlockerDimensions(double lengthM, double widthM);

  private:
    // Liang–Barsky segment-vs-AABB: true iff (x1,y1)→(x2,y2) intersects
    // the rectangle centred at (cx,cy) with half-extents (hl,hw).
    static bool SegmentIntersectsAabb(double x1, double y1,
                                      double x2, double y2,
                                      double cx, double cy,
                                      double hl, double hw);

    bool IsBlockedByVehicle(Ptr<const MobilityModel> a,
                            Ptr<const MobilityModel> b) const;

    // NS-3 attribute accessors (member-function pointer form).
    void   SetBlockerLengthM(double v) { m_blockerHalfLength = v / 2.0; }
    double GetBlockerLengthM() const   { return m_blockerHalfLength * 2.0; }
    void   SetBlockerWidthM(double v)  { m_blockerHalfWidth  = v / 2.0; }
    double GetBlockerWidthM() const    { return m_blockerHalfWidth  * 2.0; }

    Ptr<ChannelConditionModel> m_wrappedModel;
    Ptr<TraciClient>           m_traciClient;
    std::vector<std::string>   m_blockerIds;
    NodeContainer              m_blockerNodes; // used when TraCI is absent
    double m_blockerHalfLength{2.5};   // 5 m truck / 2
    double m_blockerHalfWidth{1.25};   // 2.5 m truck / 2
};

} // namespace ns3

#endif // THREE_GPP_GEOMETRIC_NLOSV_H
