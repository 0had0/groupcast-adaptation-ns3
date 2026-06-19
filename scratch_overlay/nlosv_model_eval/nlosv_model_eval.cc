/*
 * nlosv_model_eval.cc
 *
 * Standalone ns-3 probe comparing two ways of determining the V2X NLOSv
 * (Non-Line-Of-Sight due to a vehicle) channel condition:
 *
 *   Model A  — Probabilistic / density-based:
 *              ProbabilisticV2vUrban/HighwayChannelConditionModel.
 *              NLOSv is a stochastic draw whose probability is a function of
 *              2D distance and a global vehicle-density tier. It never looks
 *              at where vehicles actually are.
 *
 *   Model B  — Geometric / position-exact (treated as ground truth):
 *              ThreeGppGeometricNlosvModel. A TX->RX link is NLOSv iff another
 *              platoon vehicle's footprint geometrically intersects the
 *              TX->RX line segment (Liang-Barsky test).
 *
 * For every sampled time and every TX->RX pair the probe records the
 * geometric verdict, the empirical probabilistic distribution (Monte-Carlo
 * draws), the closed-form probabilistic distribution, and the resulting
 * pathloss in dB under each model. Output is a per-link-sample CSV.
 */

#include "common/channel/three-gpp-geometric-nlosv.h"

#include "ns3/core-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/propagation-module.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <set>
#include <sstream>
#include <string>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("NlosvModelEval");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static std::string
LosToString(ChannelCondition::LosConditionValue v)
{
    switch (v)
    {
    case ChannelCondition::LOS:   return "LOS";
    case ChannelCondition::NLOS:  return "NLOS";
    case ChannelCondition::NLOSv: return "NLOSv";
    default:                      return "ND";
    }
}

static std::vector<uint32_t>
ParseIdList(const std::string& csv)
{
    std::vector<uint32_t> out;
    std::stringstream ss(csv);
    std::string tok;
    while (std::getline(ss, tok, ','))
    {
        if (tok.empty())
            continue;
        out.push_back(static_cast<uint32_t>(std::stoul(tok)));
    }
    return out;
}

struct Probs
{
    double pLos{0.0};
    double pNlosv{0.0};
    double pNlos{0.0};
};

// Closed-form effective draw probabilities of the probabilistic V2V model.
// Mirrors ProbabilisticV2vUrban/HighwayChannelConditionModel::Compute{Plos,Pnlos}
// AND the bucket logic in ThreeGppChannelConditionModel::ComputeChannelCondition
// (pRef<=pLos -> LOS; <=pLos+pNlos -> NLOS; else -> NLOSv).
static Probs
ClosedFormProbs(const std::string& scenario, const std::string& density, double d)
{
    auto clamp01 = [](double v) { return std::min(1.0, std::max(0.0, v)); };
    Probs p;
    double pLos = 0.0;
    double pNlosModel = 0.0; // the model's ComputePnlos() return value (NLOS prob)

    if (scenario == "V2V_Urban")
    {
        double pNlosvRaw = 0.0;
        if (density == "LOW")
        {
            pLos = clamp01(0.8548 * std::exp(-0.0064 * d));
            pNlosvRaw = clamp01(1.0 / (0.0396 * d) *
                                std::exp(-std::pow(std::log(d) - 5.2718, 2) / 3.4827));
        }
        else if (density == "MEDIUM")
        {
            pLos = clamp01(0.8372 * std::exp(-0.0114 * d));
            pNlosvRaw = clamp01(1.0 / (0.0312 * d) *
                                std::exp(-std::pow(std::log(d) - 5.0063, 2) / 2.4544));
        }
        else // HIGH
        {
            pLos = clamp01(0.8962 * std::exp(-0.017 * d));
            pNlosvRaw = clamp01(1.0 / (0.0242 * d) *
                                std::exp(-std::pow(std::log(d) - 5.0115, 2) / 2.2092));
        }
        pNlosModel = 1.0 - pLos - pNlosvRaw; // not clamped, matches the model
    }
    else // V2V_Highway
    {
        double aL, bL, cL, aN, bN, cN;
        if (density == "LOW")
        {
            aL = 1.5e-6;  bL = -0.0015; cL = 1.0;
            aN = -2.9e-7; bN = 0.00059; cN = 0.0017;
        }
        else if (density == "MEDIUM")
        {
            aL = 2.7e-6;  bL = -0.0025; cL = 1.0;
            aN = -3.7e-7; bN = 0.00061; cN = 0.015;
        }
        else // HIGH
        {
            aL = 3.2e-6;  bL = -0.003;  cL = 1.0;
            aN = -4.1e-7; bN = 0.00067; cN = 0.0;
        }
        pLos = clamp01(aL * d * d + bL * d + cL);
        pNlosModel = clamp01(aN * d * d + bN * d + cN);
    }

    // Bucket widths actually realised by the draw logic.
    double pNlosEff = std::max(0.0, pNlosModel);
    pNlosEff = std::min(pNlosEff, 1.0 - pLos);
    double pNlosvEff = 1.0 - pLos - pNlosEff;

    p.pLos = pLos;
    p.pNlos = pNlosEff;
    p.pNlosv = pNlosvEff;
    return p;
}

// ---------------------------------------------------------------------------
// Trace loading
// ---------------------------------------------------------------------------

struct TraceData
{
    NodeContainer nodes;             // index == node_id
    std::set<uint32_t> presentIds;   // node ids that actually appear in the trace
    double tMin{0.0};
    double tMax{0.0};
};

static TraceData
LoadTrace(const std::string& path)
{
    std::ifstream f(path);
    NS_ABORT_MSG_IF(!f.is_open(), "trace file not found: " << path);

    // First pass: scan node ids and time range.
    TraceData td;
    uint32_t maxId = 0;
    bool first = true;
    std::string line;
    while (std::getline(f, line))
    {
        if (line.empty() || line[0] == '#')
            continue;
        if (line.rfind("time,", 0) == 0) // header
            continue;
        std::stringstream ss(line);
        std::string fld;
        double t;
        uint32_t nid;
        try
        {
            std::getline(ss, fld, ','); t = std::stod(fld);
            std::getline(ss, fld, ','); nid = static_cast<uint32_t>(std::stoul(fld));
        }
        catch (...)
        {
            continue;
        }
        maxId = std::max(maxId, nid);
        td.presentIds.insert(nid);
        if (first)
        {
            td.tMin = td.tMax = t;
            first = false;
        }
        td.tMin = std::min(td.tMin, t);
        td.tMax = std::max(td.tMax, t);
    }
    NS_ABORT_MSG_IF(first, "trace contains no data rows: " << path);

    td.nodes.Create(maxId + 1);
    for (uint32_t i = 0; i < td.nodes.GetN(); ++i)
    {
        auto wmm = CreateObject<WaypointMobilityModel>();
        td.nodes.Get(i)->AggregateObject(wmm);
    }

    // Second pass: add waypoints.
    f.clear();
    f.seekg(0);
    uint32_t rows = 0;
    while (std::getline(f, line))
    {
        if (line.empty() || line[0] == '#')
            continue;
        if (line.rfind("time,", 0) == 0)
            continue;
        std::stringstream ss(line);
        std::string fld;
        double t, x, y, spd;
        uint32_t nid;
        try
        {
            std::getline(ss, fld, ','); t = std::stod(fld);
            std::getline(ss, fld, ','); nid = static_cast<uint32_t>(std::stoul(fld));
            std::getline(ss, fld, ','); x = std::stod(fld);
            std::getline(ss, fld, ','); y = std::stod(fld);
            std::getline(ss, fld, ','); spd = std::stod(fld);
            (void)spd;
        }
        catch (...)
        {
            continue;
        }
        if (nid >= td.nodes.GetN())
            continue;
        auto wmm = td.nodes.Get(nid)->GetObject<WaypointMobilityModel>();
        wmm->AddWaypoint(Waypoint(Seconds(t), Vector(x, y, 1.5)));
        ++rows;
    }
    NS_LOG_INFO("trace loaded: " << path << " rows=" << rows
                                 << " nodes=" << td.nodes.GetN());
    return td;
}

// ---------------------------------------------------------------------------
// Evaluation context + per-sample callback
// ---------------------------------------------------------------------------

struct EvalContext
{
    TraceData* trace{nullptr};
    uint32_t txId{0};
    std::vector<uint32_t> rxIds;
    std::vector<uint32_t> blockerPool; // present ids eligible to block (= present ids)
    uint32_t numDraws{2000};
    std::string scenario;             // V2V_Urban | V2V_Highway
    std::string density;              // LOW | MEDIUM | HIGH
    std::string densityAttr;          // Low | Medium | High

    Ptr<ThreeGppGeometricNlosvModel> geo;
    Ptr<ThreeGppPropagationLossModel> lossLosModel;
    Ptr<ThreeGppPropagationLossModel> lossNlosvModel;
    Ptr<ThreeGppPropagationLossModel> lossNlosModel;
    double txPowerDbm{23.0};

    uint32_t rngSeed{1};
    uint32_t rngRun{1};

    std::ofstream* csv{nullptr};
    uint64_t rowsWritten{0};
    uint64_t geoNlosvCount{0};
};

static double
PathlossDb(Ptr<ThreeGppPropagationLossModel> m, double txDbm,
           Ptr<MobilityModel> a, Ptr<MobilityModel> b)
{
    return txDbm - m->CalcRxPower(txDbm, a, b);
}

static void
SampleAtTime(EvalContext* ctx, double sampleTime)
{
    Ptr<Node> txNode = ctx->trace->nodes.Get(ctx->txId);
    Ptr<MobilityModel> txMob = txNode->GetObject<MobilityModel>();
    Vector txPos = txMob->GetPosition();

    for (uint32_t rxId : ctx->rxIds)
    {
        if (rxId >= ctx->trace->nodes.GetN())
            continue;
        Ptr<Node> rxNode = ctx->trace->nodes.Get(rxId);
        Ptr<MobilityModel> rxMob = rxNode->GetObject<MobilityModel>();
        Vector rxPos = rxMob->GetPosition();

        double dist2d =
            ThreeGppChannelConditionModel::Calculate2dDistance(txPos, rxPos);

        // --- Geometric (Model B): blockers = every platoon vehicle except
        //     the TX and this RX. Deterministic single query. ---
        NodeContainer blockers;
        for (uint32_t bid : ctx->blockerPool)
        {
            if (bid == ctx->txId || bid == rxId)
                continue;
            if (bid < ctx->trace->nodes.GetN())
                blockers.Add(ctx->trace->nodes.Get(bid));
        }
        ctx->geo->SetBlockerNodes(blockers);
        Ptr<ChannelCondition> gCond = ctx->geo->GetChannelCondition(txMob, rxMob);
        bool geoNlosv = gCond->IsNlosv();
        std::string geoClass = LosToString(gCond->GetLosCondition());
        if (geoNlosv)
            ++ctx->geoNlosvCount;

        // --- Probabilistic (Model A): numDraws independent draws. A fresh
        //     model per draw is required because ThreeGppChannelConditionModel
        //     caches a link's condition and never re-rolls. ---
        uint32_t nLos = 0, nNlosv = 0, nNlos = 0;
        for (uint32_t d = 0; d < ctx->numDraws; ++d)
        {
            Ptr<ThreeGppChannelConditionModel> prob;
            if (ctx->scenario == "V2V_Urban")
                prob = CreateObject<ProbabilisticV2vUrbanChannelConditionModel>();
            else
                prob = CreateObject<ProbabilisticV2vHighwayChannelConditionModel>();
            prob->SetAttribute("Density", StringValue(ctx->densityAttr));
            Ptr<ChannelCondition> pc = prob->GetChannelCondition(txMob, rxMob);
            switch (pc->GetLosCondition())
            {
            case ChannelCondition::LOS:   ++nLos;   break;
            case ChannelCondition::NLOSv: ++nNlosv; break;
            case ChannelCondition::NLOS:  ++nNlos;  break;
            default: break;
            }
        }
        double invN = ctx->numDraws > 0 ? 1.0 / ctx->numDraws : 0.0;
        double pLosEmp = nLos * invN;
        double pNlosvEmp = nNlosv * invN;
        double pNlosEmp = nNlos * invN;

        // probabilistic hard prediction = most likely class
        std::string probMode = "LOS";
        if (pNlosvEmp >= pLosEmp && pNlosvEmp >= pNlosEmp)
            probMode = "NLOSv";
        else if (pNlosEmp >= pLosEmp && pNlosEmp >= pNlosvEmp)
            probMode = "NLOS";

        Probs cf = ClosedFormProbs(ctx->scenario, ctx->density, dist2d);

        // --- Pathloss dB under each condition ---
        double lossLos = PathlossDb(ctx->lossLosModel, ctx->txPowerDbm, txMob, rxMob);
        double lossNlosv = PathlossDb(ctx->lossNlosvModel, ctx->txPowerDbm, txMob, rxMob);
        double lossNlos = PathlossDb(ctx->lossNlosModel, ctx->txPowerDbm, txMob, rxMob);
        double geoLoss = geoNlosv ? lossNlosv : lossLos;
        double probExpLoss = pLosEmp * lossLos + pNlosvEmp * lossNlosv + pNlosEmp * lossNlos;
        double lossErr = probExpLoss - geoLoss;

        std::ostringstream row;
        row << sampleTime << ',' << ctx->txId << ',' << rxId << ','
            << dist2d << ','
            << txPos.x << ',' << txPos.y << ',' << rxPos.x << ',' << rxPos.y << ','
            << blockers.GetN() << ','
            << geoClass << ',' << (geoNlosv ? 1 : 0) << ','
            << pLosEmp << ',' << pNlosvEmp << ',' << pNlosEmp << ','
            << cf.pLos << ',' << cf.pNlosv << ',' << cf.pNlos << ','
            << probMode << ','
            << lossLos << ',' << lossNlosv << ',' << lossNlos << ','
            << geoLoss << ',' << probExpLoss << ',' << lossErr << ','
            << ctx->scenario << ',' << ctx->density << ',' << ctx->numDraws << ','
            << ctx->rngSeed << ',' << ctx->rngRun << '\n';
        (*ctx->csv) << row.str();
        ++ctx->rowsWritten;
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int
main(int argc, char* argv[])
{
    std::string tracePath;
    std::string baseScenario = "V2V_Urban";
    std::string density = "MEDIUM";
    uint32_t numDraws = 2000;
    uint32_t txNodeId = 0;
    std::string rxNodeIds = "1,2,3,4,5,6,7,8";
    double blockerLengthM = 5.0;
    double blockerWidthM = 2.5;
    double sampleEverySec = 0.5;
    double startSec = 0.0;
    double stopSec = 1e9;
    double txPowerDbm = 23.0;
    double freqHz = 5.9e9;
    std::string outCsv = "nlosv_model_eval.csv";
    uint32_t rngSeed = 1;
    uint32_t rngRun = 1;

    CommandLine cmd(__FILE__);
    cmd.AddValue("tracePath", "Path to the mobility trace CSV (time,node_id,x,y,speed)", tracePath);
    cmd.AddValue("baseScenario", "V2V_Urban or V2V_Highway", baseScenario);
    cmd.AddValue("density", "Probabilistic vehicle-density tier: LOW|MEDIUM|HIGH", density);
    cmd.AddValue("numDraws", "Monte-Carlo draws per link-sample for the probabilistic model", numDraws);
    cmd.AddValue("txNodeId", "Node id of the TX leader", txNodeId);
    cmd.AddValue("rxNodeIds", "Comma-separated RX follower node ids", rxNodeIds);
    cmd.AddValue("blockerLengthM", "Blocker vehicle length [m]", blockerLengthM);
    cmd.AddValue("blockerWidthM", "Blocker vehicle width [m]", blockerWidthM);
    cmd.AddValue("sampleEverySec", "Time sampling cadence [s]", sampleEverySec);
    cmd.AddValue("startSec", "First sample time [s]", startSec);
    cmd.AddValue("stopSec", "Last sample time [s]", stopSec);
    cmd.AddValue("txPowerDbm", "Reference TX power for pathloss [dBm]", txPowerDbm);
    cmd.AddValue("freqHz", "Carrier frequency for the pathloss model [Hz]", freqHz);
    cmd.AddValue("outCsv", "Output per-link-sample CSV path", outCsv);
    cmd.AddValue("rngSeed", "ns-3 RNG seed", rngSeed);
    cmd.AddValue("rngRun", "ns-3 RNG run number", rngRun);
    cmd.Parse(argc, argv);

    NS_ABORT_MSG_IF(tracePath.empty(), "--tracePath is required");
    NS_ABORT_MSG_IF(baseScenario != "V2V_Urban" && baseScenario != "V2V_Highway",
                    "--baseScenario must be V2V_Urban or V2V_Highway");
    NS_ABORT_MSG_IF(density != "LOW" && density != "MEDIUM" && density != "HIGH",
                    "--density must be LOW, MEDIUM or HIGH");

    RngSeedManager::SetSeed(rngSeed);
    RngSeedManager::SetRun(rngRun);

    TraceData trace = LoadTrace(tracePath);

    // Geometric model B: wrap an always-LOS base so its only non-LOS verdict
    // is a geometric NLOSv -> clean binary ground truth.
    auto baseLos = CreateObject<AlwaysLosChannelConditionModel>();
    auto geo = CreateObject<ThreeGppGeometricNlosvModel>();
    geo->SetWrappedModel(baseLos);
    geo->SetBlockerDimensions(blockerLengthM, blockerWidthM);

    // Fixed-condition pathloss models: one per condition. Shadowing disabled
    // so the LOS/NLOS losses are deterministic functions of geometry.
    auto makeLoss = [&](Ptr<ChannelConditionModel> ccm) -> Ptr<ThreeGppPropagationLossModel> {
        Ptr<ThreeGppPropagationLossModel> m;
        if (baseScenario == "V2V_Urban")
            m = CreateObject<ThreeGppV2vUrbanPropagationLossModel>();
        else
            m = CreateObject<ThreeGppV2vHighwayPropagationLossModel>();
        m->SetAttribute("Frequency", DoubleValue(freqHz));
        m->SetAttribute("ShadowingEnabled", BooleanValue(false));
        m->SetChannelConditionModel(ccm);
        return m;
    };
    Ptr<ThreeGppPropagationLossModel> lossLosModel =
        makeLoss(CreateObject<AlwaysLosChannelConditionModel>());
    Ptr<ThreeGppPropagationLossModel> lossNlosvModel =
        makeLoss(CreateObject<NeverLosVehicleChannelConditionModel>());
    Ptr<ThreeGppPropagationLossModel> lossNlosModel =
        makeLoss(CreateObject<NeverLosChannelConditionModel>());

    std::ofstream csv(outCsv);
    NS_ABORT_MSG_IF(!csv.is_open(), "cannot open output CSV: " << outCsv);
    csv << "time,tx_id,rx_id,dist2d,tx_x,tx_y,rx_x,rx_y,n_blockers,"
           "geo_class,geo_nlosv,"
           "p_los_emp,p_nlosv_emp,p_nlos_emp,p_los_cf,p_nlosv_cf,p_nlos_cf,"
           "prob_mode_class,"
           "loss_los_db,loss_nlosv_db,loss_nlos_db,geo_loss_db,prob_exp_loss_db,loss_err_db,"
           "base_scenario,density,num_draws,rng_seed,rng_run\n";

    EvalContext ctx;
    ctx.trace = &trace;
    ctx.txId = txNodeId;
    ctx.rxIds = ParseIdList(rxNodeIds);
    ctx.blockerPool.assign(trace.presentIds.begin(), trace.presentIds.end());
    ctx.numDraws = numDraws;
    ctx.scenario = baseScenario;
    ctx.density = density;
    ctx.densityAttr = (density == "LOW") ? "Low" : (density == "MEDIUM") ? "Medium" : "High";
    ctx.geo = geo;
    ctx.lossLosModel = lossLosModel;
    ctx.lossNlosvModel = lossNlosvModel;
    ctx.lossNlosModel = lossNlosModel;
    ctx.txPowerDbm = txPowerDbm;
    ctx.rngSeed = rngSeed;
    ctx.rngRun = rngRun;
    ctx.csv = &csv;

    double tStart = std::max(startSec, trace.tMin);
    double tStop = std::min(stopSec, trace.tMax);
    NS_ABORT_MSG_IF(tStop < tStart, "empty sampling window");

    uint32_t nSamples = 0;
    for (double t = tStart; t <= tStop + 1e-9; t += sampleEverySec)
    {
        Simulator::Schedule(Seconds(t), &SampleAtTime, &ctx, t);
        ++nSamples;
    }

    Simulator::Stop(Seconds(tStop + 1.0));
    Simulator::Run();
    Simulator::Destroy();
    csv.close();

    std::cout << "[nlosv_model_eval] trace=" << tracePath
              << " scenario=" << baseScenario << " density=" << density
              << " draws=" << numDraws << " samples=" << nSamples
              << " rows=" << ctx.rowsWritten
              << " geoNLOSv=" << ctx.geoNlosvCount
              << " -> " << outCsv << std::endl;
    return 0;
}
