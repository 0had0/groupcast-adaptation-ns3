// scratch/adaptive-phy-eval/ns3/common/eval-csv-writer.cc

#include "eval-csv-writer.h"

#include "ns3/core-module.h"
#include "ns3/log.h"

#include <filesystem>
#include <algorithm>
#include <cmath>
#include <sstream>

NS_LOG_COMPONENT_DEFINE("AdaptivePhyEval.CsvWriter");

namespace ns3::adaptive_phy_eval
{

NS_LOG_COMPONENT_DEFINE("AdaptivePhyEval.CsvWriter");

TypeId
EvalCsvWriter::GetTypeId()
{
    static TypeId tid = TypeId("ns3::adaptive_phy_eval::EvalCsvWriter")
        .SetGroupName("AdaptivePhyEval")
        .AddConstructor<EvalCsvWriter>();
    return tid;
}

EvalCsvWriter::EvalCsvWriter()
{
}

EvalCsvWriter::~EvalCsvWriter()
{
    if (m_stream.is_open())
    {
        m_stream.close();
    }
}

void
EvalCsvWriter::Init(const std::string& path, const EvalConfig& cfg)
{
    m_path = path;
    m_cfg = cfg;
    m_receivers.clear();

    // Create parent directories if needed
    std::error_code ec;
    std::filesystem::path p(path);
    if (p.has_parent_path())
    {
        std::filesystem::create_directories(p.parent_path(), ec);
    }

    // Open file and write header
    m_stream.open(path, std::ios::out | std::ios::trunc);
    if (!m_stream.is_open())
    {
        NS_LOG_ERROR("Cannot open CSV for write: " << path);
        return;
    }

    // CSV header (matching required format)
    m_stream << "scenario,condition,mu,w_prbs,mcs,seed,rx_id,prr,"
            << "sinr_mean_db,sinr_min_db,sinr_max_db,sinr_std_db,sinr_p5_db,"
            << "bler,latency_mean_ms,latency_max_ms,latency_p95_ms,"
            << "pkts_over_deadline,pkts_sent,pkts_received,resource_prbs"
            << "\n";
}

void
EvalCsvWriter::AddReceiverMetrics(const ReceiverMetrics& metrics)
{
    m_receivers.push_back(metrics);
}

void
EvalCsvWriter::Finalize()
{
    if (!m_stream.is_open())
    {
        NS_LOG_ERROR("CSV not open, cannot finalize");
        return;
    }

    double groupPrr = ComputeGroupPrr();
    uint32_t resourcePrbs = ComputeResourcePrbs();

    // Write one row per receiver
    for (const auto& rx : m_receivers)
    {
        m_stream << m_cfg.scenario << ","
                << m_cfg.condition << ","
                << (uint32_t)m_cfg.mu << ","
                << m_cfg.bandwidthPrbs << ","
                << (uint32_t)m_cfg.mcsIndex << ","
                << m_cfg.seed << ","
                << rx.rxId << ","
                << rx.prr << ","
                << rx.sinrMeanDb << ","
                << rx.sinrMinDb << ","
                << rx.sinrMaxDb << ","
                << rx.sinrStdDb << ","
                << rx.sinrP5Db << ","
                << rx.bler << ","
                << rx.latencyMeanMs << ","
                << rx.latencyMaxMs << ","
                << rx.latencyP95Ms << ","
                << rx.pktsOverDeadline << ","
                << rx.pktsSent << ","
                << rx.pktsReceived << ","
                << resourcePrbs
                << "\n";
    }

    m_stream.flush();
}

double
EvalCsvWriter::ComputeGroupPrr() const
{
    if (m_receivers.empty())
    {
        return 0.0;
    }

    // GroupPRR = min(PRR over all receivers)
    double minPrr = 1.0;
    for (const auto& rx : m_receivers)
    {
        minPrr = std::min(minPrr, rx.prr);
    }
    return minPrr;
}

uint32_t
EvalCsvWriter::ComputeResourcePrbs() const
{
    // Resource cost is deterministic from bandwidth
    // For simplicity, return the bandwidth PRBs
    return m_cfg.bandwidthPrbs;
}

} // namespace ns3::adaptive_phy_eval