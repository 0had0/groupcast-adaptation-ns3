/*
 * ThreeGppGeometricNlosvModel — implementation.
 *
 * Segment-vs-AABB intersection test (Liang–Barsky):
 *   Given a directed segment P0→P1 parameterised as P(t) = P0 + t*(P1-P0),
 *   t∈[0,1], clip it against the four half-planes that define the AABB.
 *   If the surviving t-interval is non-empty the segment intersects the box.
 *
 * Blocker footprint is axis-aligned (ignores vehicle heading).  For vehicles
 * travelling along a road this is a slight over-approximation but is
 * conservative (never misses a real block) and cheap to evaluate.
 */

#include "three-gpp-geometric-nlosv.h"

#include "ns3/double.h"
#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/node.h"
#include "ns3/pointer.h"

#include <algorithm>
#include <cmath>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("ThreeGppGeometricNlosvModel");
NS_OBJECT_ENSURE_REGISTERED(ThreeGppGeometricNlosvModel);

TypeId
ThreeGppGeometricNlosvModel::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::ThreeGppGeometricNlosvModel")
            .SetParent<ChannelConditionModel>()
            .SetGroupName("Propagation")
            .AddConstructor<ThreeGppGeometricNlosvModel>()
            .AddAttribute("WrappedModel",
                          "Underlying channel-condition model to decorate.",
                          PointerValue(),
                          MakePointerAccessor(&ThreeGppGeometricNlosvModel::m_wrappedModel),
                          MakePointerChecker<ChannelConditionModel>())
            .AddAttribute("BlockerLengthM",
                          "Blocker vehicle length [m].",
                          DoubleValue(5.0),
                          MakeDoubleAccessor(&ThreeGppGeometricNlosvModel::SetBlockerLengthM,
                                            &ThreeGppGeometricNlosvModel::GetBlockerLengthM),
                          MakeDoubleChecker<double>(0.1))
            .AddAttribute("BlockerWidthM",
                          "Blocker vehicle width [m].",
                          DoubleValue(2.5),
                          MakeDoubleAccessor(&ThreeGppGeometricNlosvModel::SetBlockerWidthM,
                                            &ThreeGppGeometricNlosvModel::GetBlockerWidthM),
                          MakeDoubleChecker<double>(0.1));
    return tid;
}

ThreeGppGeometricNlosvModel::ThreeGppGeometricNlosvModel()
    : ChannelConditionModel()
{
}

ThreeGppGeometricNlosvModel::~ThreeGppGeometricNlosvModel() = default;

void
ThreeGppGeometricNlosvModel::SetWrappedModel(Ptr<ChannelConditionModel> model)
{
    m_wrappedModel = model;
}

void
ThreeGppGeometricNlosvModel::SetTraciClient(Ptr<TraciClient> traci)
{
    m_traciClient = traci;
}

void
ThreeGppGeometricNlosvModel::SetBlockerVehicleIds(const std::vector<std::string>& ids)
{
    m_blockerIds = ids;
}

void
ThreeGppGeometricNlosvModel::SetBlockerNodes(const NodeContainer& nodes)
{
    m_blockerNodes = nodes;
}

void
ThreeGppGeometricNlosvModel::SetBlockerDimensions(double lengthM, double widthM)
{
    m_blockerHalfLength = lengthM / 2.0;
    m_blockerHalfWidth  = widthM  / 2.0;
}

// ---------------------------------------------------------------------------
// Liang–Barsky segment-vs-AABB
// ---------------------------------------------------------------------------
bool
ThreeGppGeometricNlosvModel::SegmentIntersectsAabb(double x1, double y1,
                                                    double x2, double y2,
                                                    double cx, double cy,
                                                    double hl, double hw)
{
    double dx = x2 - x1;
    double dy = y2 - y1;

    double tEnter = 0.0;
    double tExit  = 1.0;

    auto clip = [&](double p, double q) -> bool {
        if (std::abs(p) < 1e-12)
            return q >= 0.0;
        double r = q / p;
        if (p < 0.0)
            tEnter = std::max(tEnter, r);
        else
            tExit  = std::min(tExit, r);
        return tEnter <= tExit;
    };

    double xMin = cx - hl, xMax = cx + hl;
    double yMin = cy - hw, yMax = cy + hw;

    return clip(-dx, x1 - xMin) &&
           clip( dx, xMax - x1) &&
           clip(-dy, y1 - yMin) &&
           clip( dy, yMax - y1) &&
           tEnter <= tExit;
}

// ---------------------------------------------------------------------------
// Blocker check
// ---------------------------------------------------------------------------
bool
ThreeGppGeometricNlosvModel::IsBlockedByVehicle(Ptr<const MobilityModel> a,
                                                 Ptr<const MobilityModel> b) const
{
    Vector posA = a->GetPosition();
    Vector posB = b->GetPosition();

    // --- Path 1: direct NodeContainer (trace-replay mode, no TraCI) ---
    if (m_blockerNodes.GetN() > 0)
    {
        for (uint32_t i = 0; i < m_blockerNodes.GetN(); ++i)
        {
            Ptr<MobilityModel> mob = m_blockerNodes.Get(i)->GetObject<MobilityModel>();
            if (!mob)
                continue;
            Vector posV = mob->GetPosition();
            if (SegmentIntersectsAabb(posA.x, posA.y,
                                      posB.x, posB.y,
                                      posV.x, posV.y,
                                      m_blockerHalfLength, m_blockerHalfWidth))
            {
                NS_LOG_INFO("GeometricNLOSv: node-blocker " << i
                            << " at (" << posV.x << "," << posV.y
                            << ") blocks path.");
                return true;
            }
        }
        return false;
    }

    // --- Path 2: TraCI lookup by SUMO vehicle ID (default SUMO mode) ---
    if (!m_traciClient || m_blockerIds.empty())
        return false;

    for (const auto& id : m_blockerIds)
    {
        auto nodeMap = m_traciClient->get_NodeMap();
        auto it = nodeMap.find(id);
        if (it == nodeMap.end())
            continue;

        Ptr<Node> blockerNode = it->second.second;
        if (!blockerNode)
            continue;

        Ptr<MobilityModel> blockerMob = blockerNode->GetObject<MobilityModel>();
        if (!blockerMob)
            continue;

        Vector posV = blockerMob->GetPosition();

        if (SegmentIntersectsAabb(posA.x, posA.y,
                                  posB.x, posB.y,
                                  posV.x, posV.y,
                                  m_blockerHalfLength, m_blockerHalfWidth))
        {
            NS_LOG_INFO("GeometricNLOSv: blocker '" << id
                        << "' at (" << posV.x << "," << posV.y
                        << ") blocks Tx(" << posA.x << "," << posA.y
                        << ")→Rx(" << posB.x << "," << posB.y << ")");
            return true;
        }
    }
    return false;
}

// ---------------------------------------------------------------------------
// ChannelConditionModel interface
// ---------------------------------------------------------------------------
Ptr<ChannelCondition>
ThreeGppGeometricNlosvModel::GetChannelCondition(Ptr<const MobilityModel> a,
                                                  Ptr<const MobilityModel> b) const
{
    Ptr<ChannelCondition> cond;
    if (m_wrappedModel)
        cond = m_wrappedModel->GetChannelCondition(a, b);
    else
        cond = CreateObject<ChannelCondition>(ChannelCondition::LOS);

    if (!cond->IsNlos() && IsBlockedByVehicle(a, b))
        cond->SetLosCondition(ChannelCondition::NLOSv);

    return cond;
}

int64_t
ThreeGppGeometricNlosvModel::AssignStreams(int64_t stream)
{
    if (m_wrappedModel)
        return m_wrappedModel->AssignStreams(stream);
    return 0;
}

} // namespace ns3
