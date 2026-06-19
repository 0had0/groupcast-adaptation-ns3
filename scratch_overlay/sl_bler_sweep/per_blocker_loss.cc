// per_blocker_loss.cc
#include "per_blocker_loss.h"

#include "ns3/double.h"
#include "ns3/log.h"
#include "ns3/mobility-model.h"

#include <cmath>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("PerBlockerNlosvLoss");
NS_OBJECT_ENSURE_REGISTERED(PerBlockerNlosvLoss);

TypeId
PerBlockerNlosvLoss::GetTypeId()
{
    static TypeId tid = TypeId("ns3::PerBlockerNlosvLoss")
                            .SetParent<PropagationLossModel>()
                            .SetGroupName("Propagation")
                            .AddConstructor<PerBlockerNlosvLoss>();
    return tid;
}

PerBlockerNlosvLoss::PerBlockerNlosvLoss()
{
    m_logNor = CreateObject<LogNormalRandomVariable>();
}

PerBlockerNlosvLoss::~PerBlockerNlosvLoss() = default;

void
PerBlockerNlosvLoss::SetBlockerNodes(const NodeContainer& nodes)
{
    m_blockers = nodes;
}

void
PerBlockerNlosvLoss::SetBlockerDimensions(double lengthM, double widthM)
{
    m_halfLen = lengthM / 2.0;
    m_halfWidth = widthM / 2.0;
}

bool
PerBlockerNlosvLoss::SegmentIntersectsAabb(double x1, double y1,
                                           double x2, double y2,
                                           double cx, double cy,
                                           double hl, double hw)
{
    const double xmin = cx - hl;
    const double xmax = cx + hl;
    const double ymin = cy - hw;
    const double ymax = cy + hw;

    const double dx = x2 - x1;
    const double dy = y2 - y1;

    double t0 = 0.0;
    double t1 = 1.0;
    const double p[4] = {-dx, dx, -dy, dy};
    const double q[4] = {x1 - xmin, xmax - x1, y1 - ymin, ymax - y1};

    for (int i = 0; i < 4; ++i)
    {
        if (std::fabs(p[i]) < 1e-12)
        {
            // Segment parallel to this boundary: reject only if outside the slab.
            if (q[i] < 0.0)
            {
                return false;
            }
        }
        else
        {
            const double r = q[i] / p[i];
            if (p[i] < 0.0)
            {
                if (r > t1)
                {
                    return false;
                }
                if (r > t0)
                {
                    t0 = r;
                }
            }
            else
            {
                if (r < t0)
                {
                    return false;
                }
                if (r < t1)
                {
                    t1 = r;
                }
            }
        }
    }
    return true;
}

double
PerBlockerNlosvLoss::DoCalcRxPower(double txPowerDbm,
                                  Ptr<MobilityModel> a,
                                  Ptr<MobilityModel> b) const
{
    if (m_blockers.GetN() == 0)
    {
        return txPowerDbm;
    }

    const Vector pa = a->GetPosition();
    const Vector pb = b->GetPosition();

    uint32_t count = 0;
    for (uint32_t i = 0; i < m_blockers.GetN(); ++i)
    {
        Ptr<MobilityModel> mm = m_blockers.Get(i)->GetObject<MobilityModel>();
        if (!mm)
        {
            continue;
        }
        const Vector pc = mm->GetPosition();
        if (SegmentIntersectsAabb(pa.x, pa.y, pb.x, pb.y, pc.x, pc.y, m_halfLen, m_halfWidth))
        {
            ++count;
        }
    }

    // The geometric NLOSv condition already supplies the first blocker's loss
    // via the 3GPP model, so only blockers BEYOND the first add extra loss here.
    if (count <= 1)
    {
        return txPowerDbm;
    }

    const double d = CalculateDistance(pa, pb);

    // Same log-normal parameterisation as 3GPP TR 37.885 NLOSv (case 2 form):
    // mean grows with distance; sigma = 4.5 dB.
    const double muA = 9.0 + std::max(0.0, 15.0 * std::log10(std::max(d, 1.0)) - 41.0);
    const double sigmaA = 4.5;
    const double mu = std::log(std::pow(muA, 2) / std::sqrt(std::pow(sigmaA, 2) + std::pow(muA, 2)));
    const double sg = std::sqrt(std::log(std::pow(sigmaA, 2) / std::pow(muA, 2) + 1.0));
    m_logNor->SetAttribute("Mu", DoubleValue(mu));
    m_logNor->SetAttribute("Sigma", DoubleValue(sg));

    double extraLossDb = 0.0;
    for (uint32_t k = 1; k < count; ++k)
    {
        extraLossDb += std::max(0.0, m_logNor->GetValue());
    }

    return txPowerDbm - extraLossDb;
}

int64_t
PerBlockerNlosvLoss::DoAssignStreams(int64_t stream)
{
    m_logNor->SetStream(stream);
    return 1;
}

} // namespace ns3
