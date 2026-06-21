#pragma once
// kpi_hooks.h
//
// Owns all trace-statistics objects, the SQLite DB, and the V2xKpi engine.
// Responsibilities:
//   1. ConnectPhyMacTraces — hooks PSCCH/PSSCH MAC/PHY scheduling traces to
//      the DB via Config::ConnectWithoutContext (no app objects needed).
//   2. ConnectAppTraces    — hooks TxWithSeqTsSize / RxWithSeqTsSize on the
//      OnOff and PacketSink apps, writing to both the DB and a CSV file.
//   3. Flush               — calls EmptyCache() on all stats objects and
//      V2xKpi::WriteKpis() to compute PIR/PRR/throughput/collision stats.
//
// Pattern: nr-v2x-west-to-east-highway.cc:1121-1277

#include "common/flows/groupcast_flows.h"
#include "common/kpi/v2x-kpi.h"

#include "ns3/nr-module.h"
#include "ns3/stats-module.h"

#include <deque>
#include <fstream>
#include <functional>
#include <string>
#include <utility>
#include <vector>

namespace ns3
{

// Per-RX SINR feedback sink. Fired from the PSSCH-RX trace with (rnti, sinr_dB).
// Used by RA-APC-Groupcast to feed the controller without going through the DB.
using PsschSinrCb = std::function<void(uint16_t rnti, double sinrDb)>;

// Adaptation-log sink. Fired from NrSlAdaptiveMcsController on each evaluation.
using AdaptLogCb =
    std::function<void(double, double, uint8_t, uint8_t, const std::string&,
                     uint32_t, uint32_t, double, double)>;

// NACK-rate query. If installed, the recent (windowed) NACK rate can be
// computed on demand from the slHarqFeedback cache. Used by the
// CompressionController's adaptive decision tree.
using NackRateQuery = std::function<double(double /*windowMs*/)>;

// Thin wrapper around an std::ofstream + an open flag so the callback can
// null-check without opening/closing the file in the hot path.
struct PktCsv
{
    std::ofstream out;
    bool open{false};
};

// SQLite logger for SL HARQ feedback (PSFCH ACK/NACK) and HARQ-process
// timeouts (TBs that exhausted retries without an ACK). Two row-types share
// one class because they're tiny and connected at the same point.
class SlHarqFeedbackStats
{
  public:
    void SetDb(SQLiteOutput* db);
    void SaveFeedback(double timeSec,
                      uint32_t rxNodeId,
                      uint16_t rxRnti,
                      uint16_t txRnti,
                      uint32_t dstL2Id,
                      uint8_t harqId,
                      const std::string& status);
    void SaveTimeout(double timeSec, uint32_t txNodeId, uint8_t harqId);
    void EmptyCache();

    /// Compute the NACK rate (nacks / (acks+nacks)) over the last
    /// `windowMs` ms of simulator time. Returns 0.0 if no feedback in window.
    /// Reads from a small in-memory rolling buffer that is NOT drained on
    /// EmptyCache() — designed for online controller queries.
    double GetWindowedNackRate(double nowSec, double windowMs) const;

  private:
    SQLiteOutput* m_db{nullptr};
    struct FbRow
    {
        double t;
        uint32_t nodeId;
        uint16_t rxRnti;
        uint16_t txRnti;
        uint32_t dstL2Id;
        uint8_t harqId;
        std::string status;
    };
    struct ToRow
    {
        double t;
        uint32_t nodeId;
        uint8_t harqId;
    };
    std::vector<FbRow> m_fbCache;
    std::vector<ToRow> m_toCache;

    // Rolling window for online NACK-rate queries (last ~2s of feedback).
    // Pair: (timeSec, isAck).
    mutable std::deque<std::pair<double, bool>> m_recentFb;
    static constexpr double kRecentFbWindowSec = 2.0;
    static constexpr size_t kRecentFbMaxSize  = 4096;
};

// All long-lived trace objects. Lives in main() so it outlasts Simulator::Run.
struct KpiHooks
{
    explicit KpiHooks(const std::string& dbPath, const std::string& csvPath);
    ~KpiHooks() = default;

    // Not copyable: SQLiteOutput and ofstream are move-only at best.
    KpiHooks(const KpiHooks&) = delete;
    KpiHooks& operator=(const KpiHooks&) = delete;

    // -- SQLite DB (one file per run) ----------------------------------------
    SQLiteOutput db;

    // -- MAC-layer TX stats (from NrUeMac scheduling traces) -----------------
    UeMacPscchTxOutputStats pscchTxMac; // SCI-1A transmissions
    UeMacPsschTxOutputStats psschTxMac; // PSSCH data+SCI-2A transmissions

    // -- PHY-layer RX stats (from NrSpectrumPhy reception traces) ------------
    UePhyPscchRxOutputStats pscchRxPhy; // received SCI-1A
    UePhyPsschRxOutputStats psschRxPhy; // received PSSCH (TB decode outcome)

    // -- RLC RX stats (end-to-end PDU delay) ---------------------------------
    UeRlcRxOutputStats rlcRx;

    // -- Application-layer TX/RX stats (packet seq + timestamp) -------------
    UeToUePktTxRxOutputStats pktTxRx;

    // -- SL HARQ feedback (PSFCH ACK/NACK) + HARQ process timeouts ----------
    SlHarqFeedbackStats slHarq;

    // -- Optional per-packet CSV (human-readable supplement to the DB) -------
    PktCsv pktCsv;

    // -- V2X KPI engine (reads DB after sim, writes PIR/PRR/thput tables) ----
    V2xKpi v2xKpi;

    // -- Optional: per-RX SINR sink (RA-APC-Groupcast controller) -----------
    // Set before ConnectPhyMacTraces(). If non-null, the PSSCH-RX trace
    // invokes this with (rnti, sinr_dB) after writing to the DB.
    PsschSinrCb sinrSink;

    // -- Optional: adaptation-log sink (MCS controller) -----------------------
    // Set before ConnectPhyMacTraces(). If non-null, called on each MCS evaluation.
    AdaptLogCb adaptLogSink;

    // Write an adaptLog row. Called by the MCS controller callback.
    void WriteAdaptLog(double timeMs, double metric, uint8_t prevMcs, uint8_t newMcs,
                      const std::string& decision, uint32_t ackCount, uint32_t nackCount,
                      double windowStartMs, double windowEndMs);

    // Write an appPktSeq row (timeSec, txRx, nodeId, dstPort, seq) for every
    // app-layer TX/RX. Lets the relay analysis dedup direct-vs-relay copies by
    // sequence number (union PRR) and derive end-to-end age (rx_time minus the
    // leader's tx_time for that seq) — neither possible from the ns-3-dev
    // pktTxRx/avrgPrr tables (no seq, no per-dstL2Id split).
    void WriteAppPktSeq(double timeSec,
                        const std::string& txRx,
                        uint32_t nodeId,
                        uint16_t dstPort,
                        uint32_t seq);

    // Write a relaySelect row (timeSec, boundaryM, nodeId, distToTxM, seq,
    // forwarded). Called by RelayApp on each elected relay decision; captures
    // the real-time relay handoff timeline.
    void WriteRelaySelect(double timeSec,
                          double boundaryM,
                          uint32_t nodeId,
                          double distToTxM,
                          uint32_t seq,
                          int forwarded);

    // Write a relayTiming row. Called by the fixed RelayApp on each forwarded
    // leader packet: per-packet app-visible timestamps used to reconstruct the
    // relay's dynamic remaining-PDB and end-to-end latency/violations.
    void WriteRelayTiming(uint32_t seq,
                          uint16_t origPort,
                          uint32_t relayNodeId,
                          double leaderGenSec,
                          double relayDecodeSec,
                          double forwardSendSec,
                          int forwarded);

    // Write a compressionLog row. Called by the CompressionController (TX)
    // and OnUePacket (RX).
    void WriteCompressionLog(double timeSec,
                             const std::string& txRx,
                             uint32_t nodeId,
                             uint16_t flowId,
                             uint32_t pktSeq,
                             uint8_t codec,
                             uint32_t origSize,
                             uint32_t compSize,
                             uint64_t encodeUs,
                             uint64_t decodeUs,
                             double entropyEst,
                             const std::string& reason);

    // Write an rlcFrag row. Called by the CompressionController per TX.
    void WriteRlcFragLog(double timeSec,
                         uint32_t txNodeId,
                         uint32_t pktSeq,
                         uint32_t origSdu,
                         uint32_t wireBytes,
                         uint32_t nSegmentsPre,
                         uint32_t nSegmentsPost,
                         uint32_t tbSize);

    // Compute the NACK rate from the slHarqFeedback cache over the last
    // `windowMs` milliseconds of simulator time. Used by the CompressionController.
    double RecentNackRate(double windowMs) const;

  private:
    std::string m_dbBase; // path without ".db" suffix (used by V2xKpi)
};

// Connect all PHY/MAC scheduling trace sources to the DB.
// Must be called after NrSlHelper::PrepareUeForSidelink.
void ConnectPhyMacTraces(KpiHooks& h);

// Connect TxWithSeqTsSize / RxWithSeqTsSize on every client and server app
// to both the DB (pktTxRx table) and the optional CSV file.
// `flows.clients[f]` and `flows.servers[f]` must already be installed.
void ConnectAppTraces(KpiHooks& h, const FlowResult& flows);

// Snapshot node positions into the nodeInfo DB table and into the V2xKpi
// IP-to-position map.  Must be called BEFORE Simulator::Destroy() while
// NodeList is still populated (i.e. after Simulator::Run() returns but
// before ::Destroy()).
void PreFlush(KpiHooks& h);

// Drain all DB caches and write KPI tables.  Call after Simulator::Destroy().
// PreFlush() must have been called first.
void Flush(KpiHooks& h);

} // namespace ns3
