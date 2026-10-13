// helper_select.cc -- see helper_select.h

#include "helper_select.h"

#include "ns3/building-list.h"
#include "ns3/building.h"

#include <algorithm>
#include <cmath>

namespace ns3
{

namespace
{

double
Phi(double x)
{
    return 0.5 * std::erfc(-x / std::sqrt(2.0));
}

} // namespace

void
HelperSelector::Init(const Config& cfg,
                     const NodeContainer& nodes,
                     uint32_t numMembers,
                     Ptr<ThreeGppGeometricNlosvModel> geom)
{
    m_cfg = cfg;
    m_nodes = nodes;
    m_numMembers = numMembers;
    m_geom = geom;
    if (m_cfg.policy != "static")
    {
        Simulator::Schedule(Seconds(0), &HelperSelector::Snapshot, this);
    }
}

void
HelperSelector::Snapshot()
{
    Cam c;
    c.t = Simulator::Now().GetSeconds();
    for (uint32_t i = 0; i <= m_numMembers; ++i)
    {
        c.pos.push_back(m_nodes.Get(i)->GetObject<MobilityModel>()->GetPosition());
    }
    m_cams.push_back(std::move(c));
    // keep what the decision delay may still need
    while (m_cams.size() > 2 &&
           m_cams[1].t <= c.t - m_cfg.decisionDelayS - m_cfg.camPeriodS)
    {
        m_cams.pop_front();
    }
    Simulator::Schedule(Seconds(m_cfg.camPeriodS), &HelperSelector::Snapshot, this);
}

double
HelperSelector::PlMaxDb(uint16_t nSubch) const
{
    if (m_cfg.plMaxDb > 0.0)
    {
        return m_cfg.plMaxDb;
    }
    // power spread over the TB's PRBs; noise of one PRB (12 x 30 kHz) + noise figure
    const double nPrb = std::max<double>(1.0, nSubch * m_cfg.subchPrbs);
    const double txPerPrb = m_cfg.txPowerDbm - 10.0 * std::log10(nPrb);
    const double noisePerPrb = -174.0 + 10.0 * std::log10(12 * 30e3) + m_cfg.noiseFigureDb;
    return txPerPrb - noisePerPrb - m_cfg.sinrReqDb;
}

double
HelperSelector::PlLos(double d) const
{
    // TR 37.885 V2V urban, LOS (and the base of NLOSv)
    return 38.77 + 16.7 * std::log10(std::max(d, 1.0)) + 18.2 * std::log10(m_cfg.fcGHz);
}

double
HelperSelector::PlNlos(double d) const
{
    // TR 37.885 V2V urban, NLOS (building)
    return 36.85 + 30.0 * std::log10(std::max(d, 1.0)) + 18.9 * std::log10(m_cfg.fcGHz);
}

int
HelperSelector::Geometry(const Cam& c, uint32_t ia, uint32_t ib) const
{
    const Vector& a = c.pos[ia];
    const Vector& b = c.pos[ib];
    for (auto it = BuildingList::Begin(); it != BuildingList::End(); ++it)
    {
        if ((*it)->IsIntersect(a, b))
        {
            return 0;
        }
    }
    // other platoon vehicles (known from their CAMs): 5 x 1.8 m, centre within 0.9 m of
    // the segment and between its ends
    const double dx = b.x - a.x;
    const double dy = b.y - a.y;
    const double len2 = dx * dx + dy * dy;
    for (uint32_t k = 0; k < c.pos.size(); ++k)
    {
        if (k == ia || k == ib || len2 <= 0.0)
        {
            continue;
        }
        const double t = ((c.pos[k].x - a.x) * dx + (c.pos[k].y - a.y) * dy) / len2;
        if (t <= 0.0 || t >= 1.0)
        {
            continue;
        }
        const double px = a.x + t * dx - c.pos[k].x;
        const double py = a.y + t * dy - c.pos[k].y;
        if (px * px + py * py <= 0.9 * 0.9)
        {
            return 1;
        }
    }
    return 2;
}

double
HelperSelector::PSuccess(const Cam& c, uint32_t ia, uint32_t ib, double plMax) const
{
    const Vector& a = c.pos[ia];
    const Vector& b = c.pos[ib];
    const double d = std::hypot(b.x - a.x, b.y - a.y);
    const double pLos = std::min(1.0, 1.05 * std::exp(-0.0114 * d));
    // P(PL + shadowing <= plMax), LOS
    const double sLos = Phi((plMax - PlLos(d)) / m_cfg.shadowLosDb);
    // NLOSv: + blockage B = max(0, N(mu, 4.5)); P(PL_LOS + B + X <= plMax), X ~ N(0, 3)
    const double mu = 9.0 + std::max(0.0, 15.0 * std::log10(std::max(d, 1.0)) - 41.0);
    const double sigB = 4.5;
    double sNlosv = Phi((0.0 - mu) / sigB) * sLos; // B = 0
    const int n = 40;
    for (int k = 0; k < n; ++k)
    {
        // B > 0 part: midpoint rule on [0, mu + 5 sigma]
        const double hi = std::max(1.0, mu + 5.0 * sigB);
        const double w = hi / n;
        const double bv = (k + 0.5) * w;
        const double pdf =
            std::exp(-0.5 * std::pow((bv - mu) / sigB, 2)) / (sigB * std::sqrt(2.0 * M_PI));
        sNlosv += pdf * w * Phi((plMax - PlLos(d) - bv) / m_cfg.shadowLosDb);
    }
    const double sNlos = Phi((plMax - PlNlos(d)) / m_cfg.shadowNlosDb);
    if (m_cfg.policy == "map")
    {
        const int geo = Geometry(c, ia, ib);
        if (geo == 0)
        {
            return sNlos;
        }
        if (geo == 1)
        {
            return sNlosv;
        }
        return pLos * sLos + (1.0 - pLos) * sNlosv;
    }
    // kuo: distance only, LOS / NLOSv mixture
    return pLos * sLos + (1.0 - pLos) * sNlosv;
}

uint32_t
HelperSelector::Choose(double now, double tNdi, uint8_t harqId, uint16_t nSubch)
{
    HelperSelectRow row;
    row.tDecide = now;
    row.tNdi = tNdi;
    row.harqId = harqId;
    row.nSubch = nSubch;
    row.chosen = m_cfg.fallback;
    row.score.assign(m_numMembers, -1.0f);
    if (m_cfg.policy == "static" || m_cams.empty())
    {
        row.applied = m_cfg.fallback;
        m_rows.push_back(row);
        return row.applied;
    }
    // newest snapshot usable after the decision delay
    size_t use = 0;
    for (size_t k = 0; k < m_cams.size(); ++k)
    {
        if (m_cams[k].t <= now - m_cfg.decisionDelayS + 1e-12)
        {
            use = k;
        }
    }
    const Cam& c = m_cams[use];
    row.camAge = now - c.t;
    const double plMax = PlMaxDb(nSubch);
    row.plMaxDb = plMax;
    const uint32_t M = m_numMembers;
    std::vector<double> pL(M + 1, 0.0); // leader -> member
    for (uint32_t r = 1; r <= M; ++r)
    {
        pL[r] = PSuccess(c, 0, r, plMax);
    }
    double best = -1.0;
    for (uint32_t i = 1; i <= M; ++i)
    {
        double sum = 0.0;
        for (uint32_t r = 1; r <= M; ++r)
        {
            if (r == i)
            {
                sum += pL[r]; // the helper itself only gets the leader's transmission
                continue;
            }
            const double pir = PSuccess(c, i, r, plMax);
            const double fail = (1.0 - pL[r]) * ((1.0 - pL[i]) + pL[i] * (1.0 - pir));
            sum += 1.0 - fail;
        }
        const double g = sum / M;
        row.score[i - 1] = static_cast<float>(g);
        if (g > best + 1e-12)
        {
            best = g;
            row.chosen = i;
        }
    }
    row.applied = m_cfg.shadow ? m_cfg.fallback : row.chosen;
    m_rows.push_back(row);
    return row.applied;
}

} // namespace ns3
