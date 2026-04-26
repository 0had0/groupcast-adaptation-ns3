// scratch/adaptive-phy-eval/ns3/common/eval-metrics-collector.cc

#include "eval-metrics-collector.h"

#include "ns3/core-module.h"
#include "ns3/log.h"
#include "ns3/simulator.h"

#include <algorithm>
#include <cmath>
#include <sstream>

NS_LOG_COMPONENT_DEFINE("AdaptivePhyEval.MetricsCollector");

// Integration with groupcast_eval
#include "groupcast_eval/logging/packet_tracker.h"
#include "groupcast_eval/logging/trace_logger.h"

namespace ns3::adaptive_phy_eval
{

NS_LOG_COMPONENT_DEFINE("AdaptivePhyEval.MetricsCollector");

TypeId
EvalMetricsCollector::GetTypeId()
{
    static TypeId tid = TypeId("ns3::adaptive_phy_eval::EvalMetricsCollector")
        .SetGroupName("AdaptivePhyEval")
        .AddConstructor<EvalMetricsCollector>();
    return tid;
}

EvalMetricsCollector::EvalMetricsCollector()
{
}

EvalMetricsCollector::~EvalMetricsCollector()
{
}

void
EvalMetricsCollector::Setup(const EvalConfig& cfg)
{
    m_cfg = cfg;
    m_metrics.clear();
    // Reserve space for receivers
    m_metrics.resize(cfg.numReceivers);
    for (uint32_t i = 0; i < cfg.numReceivers; ++i)
    {
        m_metrics[i].rxId = "rx_" + std::to_string(i + 1);
        m_metrics[i].pktsSent = 0;
        m_metrics[i].pktsReceived = 0;
    }
}

void
EvalMetricsCollector::SetNodes(Ptr<Node> txNode, NodeContainer rxNodes)
{
    m_txNode = txNode;
    m_rxNodes = rxNodes;
}

void
EvalMetricsCollector::AttachTraces()
{
    NS_LOG_FUNCTION(this);

    // This would integrate with existing groupcast_eval PacketTracker
    // For now, we set up basic tracing via callback
    // The actual trace connection happens via the main scenario file
    // which uses existing groupcast_eval::TraceLogger::Connect()

    NS_LOG_INFO("Attached metrics collector for "
              << m_cfg.numReceivers << " receivers");
}

void
EvalMetricsCollector::ComputeMetrics()
{
    NS_LOG_FUNCTION(this);

    // Compute GroupPRR and other aggregate statistics
    for (uint32_t i = 0; i < m_cfg.numReceivers; ++i)
    {
        ComputePerReceiverStats(i);
    }
}

void
EvalMetricsCollector::ComputePerReceiverStats(uint32_t rxIndex)
{
    if (rxIndex >= m_metrics.size())
    {
        return;
    }

    auto& rx = m_metrics[rxIndex];

    // PRR = received / sent
    if (rx.pktsSent > 0)
    {
        rx.prr = static_cast<double>(rx.pktsReceived) / rx.pktsSent;
    }

    // Additional statistics would be computed from collected vectors
    // For now, use default values
    // The actual trace hooks would populate these in a full implementation
}

const std::vector<ReceiverMetrics>&
EvalMetricsCollector::GetMetrics() const
{
    return m_metrics;
}

double
EvalMetricsCollector::GetGroupPrr() const
{
    if (m_metrics.empty())
    {
        return 0.0;
    }

    // GroupPRR = min(PRR over all receivers)
    double minPrr = 1.0;
    for (const auto& rx : m_metrics)
    {
        minPrr = std::min(minPrr, rx.prr);
    }
    return minPrr;
}

uint32_t
EvalMetricsCollector::FindReceiverIndex(const std::string& rxId) const
{
    for (uint32_t i = 0; i < m_metrics.size(); ++i)
    {
        if (m_metrics[i].rxId == rxId)
        {
            return i;
        }
    }
    return UINT32_MAX;
}

} // namespace ns3::adaptive_phy_eval