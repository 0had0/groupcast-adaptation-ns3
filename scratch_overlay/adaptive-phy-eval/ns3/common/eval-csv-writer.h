// scratch/adaptive-phy-eval/ns3/common/eval-csv-writer.h
// CSV writer for adaptive PHY evaluation results.

#ifndef ADAPTIVE_PHY_EVAL_CSV_WRITER_H
#define ADAPTIVE_PHY_EVAL_CSV_WRITER_H

#include "eval-config.h"

#include "ns3/object.h"

#include <fstream>
#include <string>
#include <vector>

namespace ns3::adaptive_phy_eval
{

/// Per-receiver metrics for a single simulation run.
/// Matches the required CSV output format.
struct ReceiverMetrics
{
    std::string rxId;           // "rx_1", "rx_2", etc.
    double prr{0.0};          // Packet Reception Ratio
    double sinrMeanDb{0.0};      // Mean SINR in dB
    double sinrMinDb{0.0};      // Min SINR in dB
    double sinrMaxDb{0.0};     // Max SINR in dB
    double sinrStdDb{0.0};     // Std of SINR in dB
    double sinrP5Db{0.0};     // 5th percentile SINR in dB
    double bler{0.0};          // Block Error Rate
    double latencyMeanMs{0.0};  // Mean latency in ms
    double latencyMaxMs{0.0};    // Max latency in ms
    double latencyP95Ms{0.0};   // 95th percentile latency in ms
    uint32_t pktsOverDeadline{0}; // Packets exceeding deadline
    uint32_t pktsSent{0};      // Packets transmitted
    uint32_t pktsReceived{0};   // Packets received
};

/// CSV writer for evaluation results.
/// Writes one file per simulation run.
class EvalCsvWriter : public Object
{
  public:
    static TypeId GetTypeId();
    EvalCsvWriter();
    ~EvalCsvWriter() override;

    /// Initialize with output path and scenario config.
    void Init(const std::string& path, const EvalConfig& cfg);

    /// Add metrics for a single receiver.
    void AddReceiverMetrics(const ReceiverMetrics& metrics);

    /// Compute and write GroupPRR (min across receivers).
    /// Must be called after all receivers added.
    void Finalize();

  private:
    std::string m_path;
    EvalConfig m_cfg;
    std::ofstream m_stream;
    std::vector<ReceiverMetrics> m_receivers;

    /// Compute GroupPRR = min(PRR over all receivers)
    double ComputeGroupPrr() const;

    /// Get resource cost in PRBs (deterministic from μ, W)
    uint32_t ComputeResourcePrbs() const;
};

} // namespace ns3::adaptive_phy_eval

#endif // ADAPTIVE_PHY_EVAL_CSV_WRITER_H