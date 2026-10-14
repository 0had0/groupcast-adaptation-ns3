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
    return ClampProb(0.5 * std::erfc(-x / std::sqrt(2.0)));
}

} // namespace

void
HelperSelector::Init(const Config& cfg,
                     const NodeContainer& nodes,
                     uint32_t numMembers,
                     Ptr<ThreeGppGeometricNlosvModel> geom)
{
    NS_ABORT_MSG_IF(nodes.GetN() < numMembers + 1,
                    "NodeContainer does not contain leader plus all members: GetN="
                        << nodes.GetN() << " numMembers=" << numMembers);
    NS_ABORT_MSG_IF(cfg.fcGHz <= 0.0, "HelperSelector: fcGHz must be > 0 (got " << cfg.fcGHz << ")");
    NS_ABORT_MSG_IF(cfg.shadowLosDb <= 0.0, "HelperSelector: shadowLosDb must be > 0 (got " << cfg.shadowLosDb << ")");
    NS_ABORT_MSG_IF(cfg.shadowNlosDb <= 0.0, "HelperSelector: shadowNlosDb must be > 0 (got " << cfg.shadowNlosDb << ")");
    NS_ABORT_MSG_IF(cfg.subchPrbs == 0, "HelperSelector: subchPrbs must be > 0");
    NS_ABORT_MSG_IF(!std::isfinite(cfg.noiseFigureDb), "HelperSelector: noiseFigureDb must be finite");
    NS_ABORT_MSG_IF(!std::isfinite(cfg.sinrReqDb), "HelperSelector: sinrReqDb must be finite");
    NS_ABORT_MSG_IF(cfg.hstatPrior < 0.0 || !std::isfinite(cfg.hstatPrior),
                    "HelperSelector: hstatPrior must be >= 0 (got " << cfg.hstatPrior << ")");

    m_cfg = cfg;
    m_nodes = nodes;
    m_numMembers = numMembers;
    m_geom = geom;
    m_posRng.seed(cfg.prngSeed);
    if (m_cfg.policy != "static")
    {
        Simulator::Schedule(Seconds(0), &HelperSelector::Snapshot, this);
    }
}

void
HelperSelector::OnFeedback(double tS, uint32_t memberIndex, uint32_t tbGen, uint8_t kind)
{
    uint32_t member = memberIndex;
    if (member < 1 || member > m_numMembers)
    {
        // Fallback: in case a caller passed an ns-3 Node ID instead of 1..M member index
        for (uint32_t i = 1; i <= m_numMembers && i < m_nodes.GetN(); ++i)
        {
            if (m_nodes.Get(i)->GetId() == memberIndex)
            {
                member = i;
                break;
            }
        }
    }
    if (member >= 1 && member <= m_numMembers)
    {
        FeedbackKind fKind = static_cast<FeedbackKind>(kind);
        if (tbGen > 0)
        {
            // One observation per completed TB / packet delivery trial
            for (auto it = m_feedback.rbegin(); it != m_feedback.rend(); ++it)
            {
                if (it->member == member && it->tbGen == tbGen)
                {
                    if (fKind == FeedbackKind::ACK)
                    {
                        it->kind = FeedbackKind::ACK;
                        it->t = tS;
                    }
                    return;
                }
            }
        }
        m_feedback.push_back({tS, member, tbGen, fKind});
    }

    // Configurable retention policy based on sliding window duration (chronological arrival)
    const double retentionS = std::max(10.0, m_cfg.hstatWindowMs * 1e-3);
    while (!m_feedback.empty() && m_feedback.front().t < tS - retentionS)
    {
        m_feedback.pop_front();
    }
}

void
HelperSelector::Snapshot()
{
    Cam c;
    c.t = Simulator::Now().GetSeconds();
    std::normal_distribution<double> posDist(0.0, m_cfg.posErrM);
    for (uint32_t i = 0; i <= m_numMembers && i < m_nodes.GetN(); ++i)
    {
        Vector p = m_nodes.Get(i)->GetObject<MobilityModel>()->GetPosition();
        if (m_cfg.posErrM > 0.0 && i > 0)
        {
            p.x += posDist(m_posRng);
            p.y += posDist(m_posRng);
        }
        c.pos.push_back(p);
    }
    for (uint32_t i = m_numMembers + 1; i < m_nodes.GetN(); ++i)
    {
        Vector p = m_nodes.Get(i)->GetObject<MobilityModel>()->GetPosition();
        if (m_cfg.posErrM > 0.0)
        {
            p.x += posDist(m_posRng);
            p.y += posDist(m_posRng);
        }
        c.bgPos.push_back(p);
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
    const double dClamped = std::max(d, 1.0);
    if (m_cfg.scenario == "highway")
    {
        // 3GPP TR 37.885 V2V Highway LOS
        return 32.4 + 20.0 * std::log10(dClamped) + 20.0 * std::log10(m_cfg.fcGHz);
    }
    // 3GPP TR 37.885 V2V Urban LOS
    return 38.77 + 16.7 * std::log10(dClamped) + 18.2 * std::log10(m_cfg.fcGHz);
}

double
HelperSelector::PlNlos(double d) const
{
    const double dClamped = std::max(d, 1.0);
    if (m_cfg.scenario == "highway")
    {
        // 3GPP TR 37.885 V2V Highway NLOS
        return 32.4 + 30.0 * std::log10(dClamped) + 20.0 * std::log10(m_cfg.fcGHz);
    }
    // 3GPP TR 37.885 V2V Urban NLOS (building)
    return 36.85 + 30.0 * std::log10(dClamped) + 18.9 * std::log10(m_cfg.fcGHz);
}

int
HelperSelector::Geometry(const Cam& c, uint32_t ia, uint32_t ib, uint32_t& bldCount) const
{
    const Vector& a = c.pos[ia];
    const Vector& b = c.pos[ib];
    for (auto it = BuildingList::Begin(); it != BuildingList::End(); ++it)
    {
        ++bldCount;
        if ((*it)->IsIntersect(a, b))
        {
            return 0;
        }
    }
    // other platoon vehicles and background vehicles: 5 x 1.8 m, centre within 0.9 m of
    // the segment and between its ends (2D geometric obstruction approximation)
    const double dx = b.x - a.x;
    const double dy = b.y - a.y;
    const double len2 = dx * dx + dy * dy;
    if (len2 <= 0.0)
    {
        return 2;
    }
    const double halfWidth = 0.9; // 1.8 m vehicle blocker width / 2.0
    const double hw2 = halfWidth * halfWidth;
    auto checkBlocker = [&](const Vector& p) {
        const double t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
        if (t <= 0.0 || t >= 1.0)
        {
            return false;
        }
        const double px = a.x + t * dx - p.x;
        const double py = a.y + t * dy - p.y;
        return (px * px + py * py <= hw2);
    };

    for (uint32_t k = 0; k < c.pos.size(); ++k)
    {
        if (k == ia || k == ib)
        {
            continue;
        }
        if (checkBlocker(c.pos[k]))
        {
            return 1;
        }
    }
    for (const auto& bg : c.bgPos)
    {
        if (checkBlocker(bg))
        {
            return 1;
        }
    }
    return 2;
}

double
HelperSelector::PSuccess(const Cam& c, uint32_t ia, uint32_t ib, double plMax, uint32_t& linkCount, uint32_t& bldCount) const
{
    NS_ABORT_MSG_IF(ia >= c.pos.size() || ib >= c.pos.size(),
                    "HelperSelector::PSuccess: Invalid CAM node index: ia=" << ia
                        << " ib=" << ib << " size=" << c.pos.size());
    ++linkCount;
    const Vector& a = c.pos[ia];
    const Vector& b = c.pos[ib];
    const double d = std::hypot(b.x - a.x, b.y - a.y);
    const double pLos = ClampProbability(1.05 * std::exp(-0.0114 * d));
    // P(PL + shadowing <= plMax), LOS
    const double sLos = Phi((plMax - PlLos(d)) / m_cfg.shadowLosDb);
    // NLOSv: + blockage B = max(0, N(mu, 4.5)); P(PL_LOS + B + X <= plMax), X ~ N(0, 3)
    const double mu = 9.0 + std::max(0.0, 15.0 * std::log10(std::max(d, 1.0)) - 41.0);
    const double sigB = 4.5;
    double sNlosv = Phi((0.0 - mu) / sigB) * sLos; // B = 0

    // Numerical quadrature for positive blockage B
    const double hi = std::max(20.0, std::max(0.0, mu) + 8.0 * sigB);
    const uint32_t n = 80;
    const double w = hi / n;
    for (uint32_t k = 0; k < n; ++k)
    {
        const double bv = (k + 0.5) * w;
        const double pdf =
            std::exp(-0.5 * std::pow((bv - mu) / sigB, 2)) / (sigB * std::sqrt(2.0 * kPi));
        sNlosv += pdf * w * Phi((plMax - PlLos(d) - bv) / m_cfg.shadowLosDb);
    }
    sNlosv = ClampProbability(sNlosv);
    const double sNlos = Phi((plMax - PlNlos(d)) / m_cfg.shadowNlosDb);
    const bool useMap = (m_cfg.policy == "map" || m_cfg.policy == "harqMap");
    if (useMap)
    {
        const int geo = Geometry(c, ia, ib, bldCount);
        if (geo == 0)
        {
            return ClampProbability(sNlos);
        }
        if (geo == 1)
        {
            return ClampProbability(sNlosv);
        }
        return ClampProbability(pLos * sLos + (1.0 - pLos) * sNlosv);
    }
    // kuo / harqKuo / harq / default: distance only, LOS / NLOSv mixture
    return ClampProbability(pLos * sLos + (1.0 - pLos) * sNlosv);
}

uint32_t
HelperSelector::Choose(double now, double tNdi, uint8_t harqId, uint16_t nSubch)
{
    const auto clockStart = std::chrono::high_resolution_clock::now();
    uint32_t linkCount = 0;
    uint32_t bldCount = 0;

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
        const auto clockEnd = std::chrono::high_resolution_clock::now();
        row.cpuUs = std::chrono::duration<double, std::micro>(clockEnd - clockStart).count();
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

    // H4 / harqWorstNearest: greedy heuristic picking member nearest to the worst receiver
    if (m_cfg.policy == "harq" || m_cfg.policy == "harqWorstNearest")
    {
        const double wS = m_cfg.hstatWindowMs * 1e-3;
        std::vector<uint32_t> attempts(M + 1, 0);
        std::vector<uint32_t> failures(M + 1, 0);
        for (const auto& fb : m_feedback)
        {
            if (fb.t >= now - wS && fb.t <= now && fb.member >= 1 && fb.member <= M)
            {
                attempts[fb.member]++;
                if (fb.kind != FeedbackKind::ACK)
                {
                    failures[fb.member]++;
                }
            }
        }
        uint32_t worst = 0;
        double maxFailRate = -1.0;
        for (uint32_t r = 1; r <= M; ++r)
        {
            if (attempts[r] > 0)
            {
                double rate = ClampProbability(static_cast<double>(failures[r]) / attempts[r]);
                if (rate > maxFailRate || (std::abs(rate - maxFailRate) < 1e-9 && r > worst))
                {
                    maxFailRate = rate;
                    worst = r;
                }
            }
        }
        if (worst == 0 || maxFailRate < 1e-6)
        {
            row.chosen = m_cfg.fallback; // all 0 failures or no data -> fallback (member 4)
        }
        else
        {
            // Pick member nearest to worst, preferring one ahead of it (closer to leader)
            uint32_t bestH = m_cfg.fallback;
            double minDist = 1e9;
            for (uint32_t h = 1; h <= M; ++h)
            {
                if (h == worst)
                {
                    continue;
                }
                const double d = std::hypot(c.pos[h].x - c.pos[worst].x, c.pos[h].y - c.pos[worst].y);
                if (d < minDist - 1e-3)
                {
                    minDist = d;
                    bestH = h;
                }
                else if (std::abs(d - minDist) <= 1e-3)
                {
                    // Tie: prefer one ahead of worst (smaller index, closer to leader)
                    if (h < worst && bestH >= worst)
                    {
                        bestH = h;
                    }
                }
            }
            row.chosen = bestH;
        }
        row.applied = m_cfg.shadow ? m_cfg.fallback : row.chosen;
        const auto clockEnd = std::chrono::high_resolution_clock::now();
        row.cpuUs = std::chrono::duration<double, std::micro>(clockEnd - clockStart).count();
        row.nLinkEval = linkCount;
        row.nBldTest = bldCount;
        m_rows.push_back(row);
        return row.applied;
    }

    // Kuo / Map / HarqKuo / HarqMap: evaluate expected GPRR
    // Precompute link probability matrix O(M^2) once before the candidate selection loop
    std::vector<std::vector<double>> pLink(M + 1, std::vector<double>(M + 1, 0.0));
    for (uint32_t a = 0; a <= M; ++a)
    {
        for (uint32_t b = 1; b <= M; ++b)
        {
            if (a != b)
            {
                pLink[a][b] = ClampProbability(PSuccess(c, a, b, plMax, linkCount, bldCount));
            }
        }
    }

    std::vector<double> pL(M + 1, 0.0); // direct leader -> member links
    const bool useHarqStats = (m_cfg.policy == "harqKuo" || m_cfg.policy == "harqMap");
    const double wS = m_cfg.hstatWindowMs * 1e-3;
    const double n0 = m_cfg.hstatPrior;
    std::vector<uint32_t> attempts(M + 1, 0);
    std::vector<uint32_t> acks(M + 1, 0);
    if (useHarqStats)
    {
        for (const auto& fb : m_feedback)
        {
            if (fb.t >= now - wS && fb.t <= now && fb.member >= 1 && fb.member <= M)
            {
                attempts[fb.member]++;
                if (fb.kind == FeedbackKind::ACK)
                {
                    acks[fb.member]++;
                }
            }
        }
    }

    for (uint32_t r = 1; r <= M; ++r)
    {
        const double pModel = pLink[0][r];
        if (useHarqStats)
        {
            const double nr = static_cast<double>(attempts[r]);
            const double kr = static_cast<double>(acks[r]);
            if (n0 > 0.0)
            {
                pL[r] = ClampProbability((kr + n0 * pModel) / (nr + n0));
            }
            else if (nr > 0.0)
            {
                pL[r] = ClampProbability(kr / nr);
            }
            else
            {
                // Purely empirical mode (hstatPrior == 0) with zero observations fallback
                pL[r] = pModel;
            }
        }
        else
        {
            pL[r] = pModel;
        }
    }

    // Kuo (2021) objective: pResource = 1.0 (reproduces eq. 17 faithfully)
    const double pResource = m_cfg.pResource;
    double best = -1.0;
    for (uint32_t i = 1; i <= M; ++i)
    {
        double sum = 0.0;
        for (uint32_t r = 1; r <= M; ++r)
        {
            if (r == i)
            {
                sum += pL[r];
                continue;
            }
            const double pir = pLink[i][r];
            const double pRelay = ClampProbability(pResource * pir);
            const double pSuccess = pL[r] + (1.0 - pL[r]) * pL[i] * pRelay;
            sum += ClampProbability(pSuccess);
        }
        const double g = ClampProbability(sum / M);
        row.score[i - 1] = static_cast<float>(g);
        // Deterministic tie-breaking: higher direct link pL[i], then lower index
        if (g > best + 1e-12 || (std::abs(g - best) <= 1e-12 && pL[i] > pL[row.chosen]))
        {
            best = g;
            row.chosen = i;
        }
    }
    row.applied = m_cfg.shadow ? m_cfg.fallback : row.chosen;
    const auto clockEnd = std::chrono::high_resolution_clock::now();
    row.cpuUs = std::chrono::duration<double, std::micro>(clockEnd - clockStart).count();
    row.nLinkEval = linkCount;
    row.nBldTest = bldCount;
    m_rows.push_back(row);
    return row.applied;
}

} // namespace ns3
