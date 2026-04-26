// scratch/adaptive-phy-eval/ns3/common/eval-metrics-collector.h
// Metrics collector for adaptive PHY evaluation.
// Integrates with existing groupcast_eval infrastructure.

#ifndef ADAPTIVE_PHY_EVAL_METRICS_COLLECTOR_H
#define ADAPTIVE_PHY_EVAL_METRICS_COLLECTOR_H

#include "eval-config.h"
#include "eval-csv-writer.h"

#include "ns3/object.h"
#include "ns3/node-container.h"
#include "ns3/nstime.h"

#include <string>
#include <vector>

namespace ns3::adaptive_phy_eval
{

/// Collects per-receiver metrics during simulation.
/// Integrates with existing groupcast_eval PacketTracker and TraceLogger.
class EvalMetricsCollector : public Object
{
  public:
    static TypeId GetTypeId();
    EvalMetricsCollector();
    ~EvalMetricsCollector() override;

    /// Initialize with configuration.
    void Setup(const EvalConfig& cfg);

    /// Set TX and RX nodes for metric collection.
    void SetNodes(Ptr<Node> txNode, NodeContainer rxNodes);

    /// Attach to existing groupcast_eval traces for metric collection.
    /// This uses the existing trace sources from groupcast_eval.
    void AttachTraces();

    /// Called at end of simulation to compute metrics.
    void ComputeMetrics();

    /// Get computed metrics for all receivers.
    const std::vector<ReceiverMetrics>& GetMetrics() const;

    /// Get GroupPRR (min across receivers).
    double GetGroupPrr() const;

  private:
    EvalConfig m_cfg;
    Ptr<Node> m_txNode;
    NodeContainer m_rxNodes;
    std::vector<ReceiverMetrics> m_metrics;

    /// Compute statistics from collected data.
    void ComputePerReceiverStats(uint32_t rxIndex);

    /// Helper to find receiver by ID
    uint32_t FindReceiverIndex(const std::string& rxId) const;
};

} // namespace ns3::adaptive_phy_eval

#endif // ADAPTIVE_PHY_EVAL_METRICS_COLLECTOR_H