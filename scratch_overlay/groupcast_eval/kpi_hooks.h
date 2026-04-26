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

#include "groupcast_flows.h"
#include "v2x-kpi.h"

#include "ns3/nr-module.h"
#include "ns3/stats-module.h"

#include <fstream>
#include <string>
#include <vector>

namespace ns3
{

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

// Drain all DB caches and write KPI tables.  Call after Simulator::Run().
void Flush(KpiHooks& h);

} // namespace ns3
