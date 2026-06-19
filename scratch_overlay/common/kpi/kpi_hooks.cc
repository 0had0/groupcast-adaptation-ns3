// kpi_hooks.cc

#include "kpi_hooks.h"

#include "ns3/abort.h"
#include "ns3/ipv4-l3-protocol.h"
#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/node-list.h"
#include "ns3/nr-phy-mac-common.h"

#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <sstream>
#include <unistd.h>

// Optional compression codec libraries. The compress binary's CMakeLists
// defines KPI_HOOKS_HAVE_CODEC_LIBS=1 (and links libzstd/liblz4) so the
// RX-side decompression branch in OnUePacket activates. Legacy binaries
// (traffic_class_eval, csi_groupcast_eval, traffic_class_adaptive_eval) do
// not define the macro and do not link the codecs — the magic-prefix
// detection still runs but the decompression step is elided.
#ifdef KPI_HOOKS_HAVE_CODEC_LIBS
#  include <lz4.h>
#  include <zstd.h>
#endif

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("GroupcastEval.KpiHooks");

// ---------------------------------------------------------------------------
// Trace-sink free functions
// (signatures must match exactly what NS-3 passes via MakeBoundCallback)
// ---------------------------------------------------------------------------

// PSCCH (SCI-1A) MAC TX → saves resource allocation meta-data.
static void
OnSlPscchScheduling(UeMacPscchTxOutputStats* s, const SlPscchUeMacStatParameters p)
{
    s->Save(p);
}

// PSSCH (data + SCI-2A) MAC TX → saves grant parameters.
static void
OnSlPsschScheduling(UeMacPsschTxOutputStats* s, const SlPsschUeMacStatParameters p)
{
    s->Save(p);
}

// PSCCH PHY RX → saves decoded SCI-1A info.
static void
OnSlPscchRx(UePhyPscchRxOutputStats* s, const SlRxCtrlPacketTraceParams p)
{
    s->Save(p);
}

// PSSCH PHY RX → saves TB decode outcome (success/BLER) and SINR; also
// feeds per-RX SINR (in dB) to an optional sink (RA-APC controller).
static void
OnSlPsschRx(UePhyPsschRxOutputStats* s,
            PsschSinrCb* sink,
            const SlRxDataPacketTraceParams p)
{
    s->Save(p);
    if (sink && *sink && p.m_sinr > 0.0)
    {
        const double sinrDb = 10.0 * std::log10(p.m_sinr);
        (*sink)(p.m_rnti, sinrDb);
    }
}

// RLC RX PDU → saves end-to-end PDU delay from TX RLC to RX RLC.
static void
OnSlRlcPduRx(UeRlcRxOutputStats* s,
             uint64_t imsi,
             uint16_t rnti,
             uint16_t txRnti,
             uint8_t lcid,
             uint32_t rxPduSize,
             double delay)
{
    s->Save(imsi, rnti, txRnti, lcid, rxPduSize, delay);
}

// Application TX/RX → saves packet to the DB and optionally to the CSV.
// `flowId` is the UDP destination port number (8001/8002/8003).
//
// `h` is a pointer to the parent KpiHooks (may be nullptr). When non-null
// and the packet carries a CompressionTag (codec != NONE), the receiver
// path decompresses the wire bytes (timing the operation) and logs a row
// to the compressionLog table; the pktTxRx row records the *original*
// (pre-compression) SDU size for clean PRR/PIR/throughput accounting.
static void
OnUePacket(KpiHooks* h,
           UeToUePktTxRxOutputStats* dbStats,
           PktCsv* csv,
           Ptr<Node> node,
           Ipv4Address localAddrs,
           uint16_t flowId,
           std::string txRx,
           Ptr<const Packet> p,
           const Address& srcAddrs,
           const Address& dstAddrs,
           const SeqTsSizeHeader& hdr)
{
    const uint32_t nodeId = node->GetId();
    const uint64_t imsi = node->GetDevice(0)->GetObject<NrUeNetDevice>()->GetImsi();
    const uint32_t seq = hdr.GetSeq();

    // Default accounting (unchanged behaviour for non-compression flows).
    // hdr.GetSize() is the *wire* size set by the sender — required by
    // PacketSink's stream-reassembly logic. We override below if magic is
    // detected, so pktTxRx rows record the original application-level SDU
    // size for clean PRR/PIR/throughput comparisons.
    uint32_t pktSize = hdr.GetSize();

    // ── Compression detection via inline magic-prefixed header ─────────
    //
    // PacketTags do NOT survive RLC reassembly, so the CompressionController
    // prefixes the wire bytes with:
    //   [MAGIC(4) | codec(1) | origSize(4) | encodeUs(8)]   = 17 bytes
    //
    // At TX the trace fires after AddHeader(), so the magic is at offset
    // hdr.GetSerializedSize() in the Packet buffer. At RX, PacketSink has
    // already stripped the SeqTsSizeHeader, so the magic is at offset 0.
    constexpr uint32_t COMP_MAGIC      = 0xCAFEC07Eu;
    constexpr uint32_t COMP_PREFIX_LEN = 17;

    [[maybe_unused]] uint8_t  codec    = 0;
    [[maybe_unused]] uint32_t origSize = 0;
    [[maybe_unused]] uint64_t encUsTx  = 0;
    [[maybe_unused]] uint32_t compSize = 0;
    [[maybe_unused]] bool     isCompressed = false;

    {
        const uint32_t headerOff   = (txRx == "tx") ? hdr.GetSerializedSize() : 0;
        const uint32_t bytesNeeded = headerOff + COMP_PREFIX_LEN;
        if (p->GetSize() >= bytesNeeded)
        {
            // Copy from offset 0 the bytes we need to also see headerOff.
            std::vector<uint8_t> buf(bytesNeeded);
            p->CopyData(buf.data(), bytesNeeded);
            const uint8_t* pfx = buf.data() + headerOff;
            const uint32_t magic =
                  static_cast<uint32_t>(pfx[0])
                | (static_cast<uint32_t>(pfx[1]) << 8)
                | (static_cast<uint32_t>(pfx[2]) << 16)
                | (static_cast<uint32_t>(pfx[3]) << 24);
            if (magic == COMP_MAGIC)
            {
                isCompressed = true;
                codec    = pfx[4];
                origSize = static_cast<uint32_t>(pfx[5])
                         | (static_cast<uint32_t>(pfx[6]) << 8)
                         | (static_cast<uint32_t>(pfx[7]) << 16)
                         | (static_cast<uint32_t>(pfx[8]) << 24);
                for (int i = 0; i < 8; ++i)
                    encUsTx |= static_cast<uint64_t>(pfx[9 + i]) << (i * 8);
                compSize = (txRx == "rx")
                              ? (p->GetSize() - COMP_PREFIX_LEN)
                              : (p->GetSize() - headerOff - COMP_PREFIX_LEN);
                // Account application-layer SDU size for downstream KPIs.
                pktSize = origSize + hdr.GetSerializedSize();
            }
        }
    }

#ifdef KPI_HOOKS_HAVE_CODEC_LIBS
    if (isCompressed && txRx == "rx" && codec != 0 /*CODEC_NONE*/)
    {
        // Copy the full packet payload then strip the magic prefix to get
        // the compressed bytes.
        const uint32_t wireSz = compSize;
        std::vector<uint8_t> full(p->GetSize());
        p->CopyData(full.data(), full.size());
        std::vector<uint8_t> wire(full.begin() + COMP_PREFIX_LEN, full.end());
        std::vector<uint8_t> decoded(origSize);

        const auto t0 = std::chrono::steady_clock::now();
        uint64_t decUs = 0;
        if (codec == 1 /*LZ4*/)
        {
            const int n = LZ4_decompress_safe(reinterpret_cast<const char*>(wire.data()),
                                              reinterpret_cast<char*>(decoded.data()),
                                              static_cast<int>(wireSz),
                                              static_cast<int>(origSize));
            if (n < 0) NS_LOG_WARN("LZ4_decompress_safe failed: n=" << n);
        }
        else // ZSTD_L1 / ZSTD_L3 (codec 2 or 3)
        {
            const size_t n = ZSTD_decompress(decoded.data(), origSize,
                                             wire.data(), wireSz);
            if (ZSTD_isError(n))
                NS_LOG_WARN("ZSTD_decompress failed: " << ZSTD_getErrorName(n));
        }
        const auto t1 = std::chrono::steady_clock::now();
        decUs = static_cast<uint64_t>(
            std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count());

        if (h)
        {
            h->WriteCompressionLog(Simulator::Now().GetSeconds(),
                                   "rx",
                                   nodeId,
                                   flowId,
                                   seq,
                                   codec,
                                   origSize,
                                   compSize,
                                   encUsTx,
                                   decUs,
                                   0.0,
                                   "");
        }
    }
#endif // KPI_HOOKS_HAVE_CODEC_LIBS

    dbStats->Save(txRx, localAddrs, nodeId, imsi, pktSize, srcAddrs, dstAddrs, seq);

    // Per-packet seq log (relay analysis: union PRR + end-to-end age).
    if (h)
        h->WriteAppPktSeq(Simulator::Now().GetSeconds(), txRx, nodeId, flowId, seq);

    if (csv && csv->open)
    {
        const double now = Simulator::Now().GetSeconds();
        const double delayMs =
            (txRx == "rx") ? (Simulator::Now() - hdr.GetTs()).GetMilliSeconds() : -1.0;

        csv->out << std::fixed << std::setprecision(6) << now << ',' << nodeId << ',' << flowId
                 << ',' << localAddrs << ',' << pktSize << ',' << seq << ',' << delayMs << ','
                 << txRx << '\n';
    }
}

// ---------------------------------------------------------------------------
// SlHarqFeedbackStats — SQLite logger for PSFCH ACK/NACK and HARQ timeouts.
// ---------------------------------------------------------------------------

void
SlHarqFeedbackStats::SetDb(SQLiteOutput* db)
{
    m_db = db;
    NS_ABORT_UNLESS(m_db->SpinExec(
        "CREATE TABLE IF NOT EXISTS slHarqFeedback ("
        "timeSec DOUBLE NOT NULL, "
        "rxNodeId INTEGER NOT NULL, "
        "rxRnti INTEGER NOT NULL, "
        "txRnti INTEGER NOT NULL, "
        "dstL2Id INTEGER NOT NULL, "
        "harqId INTEGER NOT NULL, "
        "status TEXT NOT NULL"
        ");"));
    NS_ABORT_UNLESS(m_db->SpinExec(
        "CREATE TABLE IF NOT EXISTS slHarqTimeout ("
        "timeSec DOUBLE NOT NULL, "
        "txNodeId INTEGER NOT NULL, "
        "harqId INTEGER NOT NULL"
        ");"));
}

void
SlHarqFeedbackStats::SaveFeedback(double timeSec,
                                  uint32_t rxNodeId,
                                  uint16_t rxRnti,
                                  uint16_t txRnti,
                                  uint32_t dstL2Id,
                                  uint8_t harqId,
                                  const std::string& status)
{
    m_fbCache.push_back({timeSec, rxNodeId, rxRnti, txRnti, dstL2Id, harqId, status});
    if (m_fbCache.size() > 4096)
    {
        EmptyCache();
    }

    // Online rolling buffer for the CompressionController and similar
    // controllers that need a recent NACK rate. Keep ~2 s of feedback.
    const bool isAck = (status == "ACK");
    m_recentFb.emplace_back(timeSec, isAck);
    // Trim by age first, then by size.
    while (!m_recentFb.empty() &&
           m_recentFb.front().first < timeSec - kRecentFbWindowSec)
    {
        m_recentFb.pop_front();
    }
    while (m_recentFb.size() > kRecentFbMaxSize)
    {
        m_recentFb.pop_front();
    }
}

double
SlHarqFeedbackStats::GetWindowedNackRate(double nowSec, double windowMs) const
{
    if (m_recentFb.empty()) return 0.0;
    const double tMin = nowSec - windowMs / 1000.0;
    uint32_t acks = 0;
    uint32_t nacks = 0;
    for (auto it = m_recentFb.rbegin(); it != m_recentFb.rend(); ++it)
    {
        if (it->first < tMin) break;
        if (it->second) ++acks; else ++nacks;
    }
    const uint32_t total = acks + nacks;
    if (total == 0) return 0.0;
    return static_cast<double>(nacks) / static_cast<double>(total);
}

void
SlHarqFeedbackStats::SaveTimeout(double timeSec, uint32_t txNodeId, uint8_t harqId)
{
    m_toCache.push_back({timeSec, txNodeId, harqId});
    if (m_toCache.size() > 4096)
    {
        EmptyCache();
    }
}

void
SlHarqFeedbackStats::EmptyCache()
{
    if (!m_db)
    {
        return;
    }
    if (!m_fbCache.empty())
    {
        NS_ABORT_UNLESS(m_db->SpinExec("BEGIN TRANSACTION;"));
        for (const auto& r : m_fbCache)
        {
            sqlite3_stmt* stmt;
            if (!m_db->SpinPrepare(&stmt, "INSERT INTO slHarqFeedback VALUES (?,?,?,?,?,?,?);") || !stmt)
            {
                NS_LOG_ERROR("Failed to prepare slHarqFeedback statement");
                continue;
            }
            if (!m_db->Bind(stmt, 1, r.t)) { NS_LOG_ERROR("Bind 1 failed"); continue; }
            if (!m_db->Bind(stmt, 2, r.nodeId)) { NS_LOG_ERROR("Bind 2 failed"); continue; }
            if (!m_db->Bind(stmt, 3, static_cast<uint32_t>(r.rxRnti))) { NS_LOG_ERROR("Bind 3 failed"); continue; }
            if (!m_db->Bind(stmt, 4, static_cast<uint32_t>(r.txRnti))) { NS_LOG_ERROR("Bind 4 failed"); continue; }
            if (!m_db->Bind(stmt, 5, r.dstL2Id)) { NS_LOG_ERROR("Bind 5 failed"); continue; }
            if (!m_db->Bind(stmt, 6, static_cast<uint32_t>(r.harqId))) { NS_LOG_ERROR("Bind 6 failed"); continue; }
            if (!m_db->Bind(stmt, 7, r.status)) { NS_LOG_ERROR("Bind 7 failed"); continue; }
            m_db->SpinExec(stmt);
        }
        NS_ABORT_UNLESS(m_db->SpinExec("END TRANSACTION;"));
        m_fbCache.clear();
    }
    if (!m_toCache.empty())
    {
        NS_ABORT_UNLESS(m_db->SpinExec("BEGIN TRANSACTION;"));
        for (const auto& r : m_toCache)
        {
            sqlite3_stmt* stmt;
            if (!m_db->SpinPrepare(&stmt, "INSERT INTO slHarqTimeout VALUES (?,?,?);") || !stmt)
            {
                NS_LOG_ERROR("Failed to prepare slHarqTimeout statement");
                continue;
            }
            if (!m_db->Bind(stmt, 1, r.t)) { NS_LOG_ERROR("Bind 1 failed"); continue; }
            if (!m_db->Bind(stmt, 2, r.nodeId)) { NS_LOG_ERROR("Bind 2 failed"); continue; }
            if (!m_db->Bind(stmt, 3, static_cast<uint32_t>(r.harqId))) { NS_LOG_ERROR("Bind 3 failed"); continue; }
            m_db->SpinExec(stmt);
        }
        NS_ABORT_UNLESS(m_db->SpinExec("END TRANSACTION;"));
        m_toCache.clear();
    }
}

// Extracts the integer right after "/NodeList/" in a Config trace context.
static uint32_t
NodeIdFromContext(const std::string& ctx)
{
    const std::string prefix = "/NodeList/";
    auto p = ctx.find(prefix);
    if (p == std::string::npos)
    {
        return 0;
    }
    return static_cast<uint32_t>(std::strtoul(ctx.c_str() + p + prefix.size(), nullptr, 10));
}

static void
OnSlHarqFeedback(SlHarqFeedbackStats* s, std::string context, const SlHarqInfo& info)
{
    const std::string status = (info.m_harqStatus == SlHarqInfo::ACK) ? "ACK" : "NACK";
    s->SaveFeedback(Simulator::Now().GetSeconds(),
                    NodeIdFromContext(context),
                    info.m_rnti,
                    info.m_txRnti,
                    info.m_dstL2Id,
                    info.m_harqProcessId,
                    status);
}

static void
OnSlHarqTimeout(SlHarqFeedbackStats* s, std::string context, uint8_t harqId)
{
    s->SaveTimeout(Simulator::Now().GetSeconds(), NodeIdFromContext(context), harqId);
}

// ---------------------------------------------------------------------------
// KpiHooks constructor
// ---------------------------------------------------------------------------

KpiHooks::KpiHooks(const std::string& dbPath, const std::string& csvPath)
    : db(dbPath + ".db"),
      m_dbBase(dbPath)
{
    // Bind each stats object to its table in the DB.
    pscchTxMac.SetDb(&db, "pscchTxUeMac");
    psschTxMac.SetDb(&db, "psschTxUeMac");
    pscchRxPhy.SetDb(&db, "pscchRxUePhy");
    psschRxPhy.SetDb(&db, "psschRxUePhy");
    rlcRx.SetDb(&db, "rlcRx");
    pktTxRx.SetDb(&db, "pktTxRx");
    slHarq.SetDb(&db);

    if (!csvPath.empty())
    {
        pktCsv.out.open(csvPath);
        if (pktCsv.out.is_open())
        {
            pktCsv.open = true;
            pktCsv.out << "time_s,nodeId,flowId,localIp,pktSize,seq,delay_ms,event\n";
        }
        else
        {
            NS_LOG_ERROR("Cannot open packet CSV: " << csvPath);
        }
    }

    // Create adaptLog table for MCS adaptation logging
    db.SpinExec(
        "CREATE TABLE IF NOT EXISTS adaptLog ("
        "timeMs REAL NOT NULL, "
        "metric REAL NOT NULL, "
        "prevMcs INTEGER NOT NULL, "
        "newMcs INTEGER NOT NULL, "
        "decision TEXT NOT NULL, "
        "ackCount INTEGER NOT NULL, "
        "nackCount INTEGER NOT NULL, "
        "windowStartMs REAL NOT NULL, "
        "windowEndMs REAL NOT NULL"
        ");");

    // compressionLog: one row per packet event (TX or RX) when the
    // CompressionController is wired up. Stays empty in non-compression
    // binaries.
    db.SpinExec(
        "CREATE TABLE IF NOT EXISTS compressionLog ("
        "timeSec    DOUBLE  NOT NULL, "
        "txRx       TEXT    NOT NULL, "
        "nodeId     INTEGER NOT NULL, "
        "flowId     INTEGER NOT NULL, "
        "pktSeq     INTEGER NOT NULL, "
        "codec      INTEGER NOT NULL, "
        "origSize   INTEGER NOT NULL, "
        "compSize   INTEGER NOT NULL, "
        "encodeUs   INTEGER NOT NULL, "
        "decodeUs   INTEGER NOT NULL, "
        "entropyEst REAL, "
        "reason     TEXT"
        ");");

    // appPktSeq: one row per app-layer TX/RX with the packet sequence number,
    // enabling per-dstL2Id (port) union PRR and end-to-end age in the relay
    // analysis (the ns-3-dev pktTxRx table carries neither seq nor dstL2Id).
    db.SpinExec(
        "CREATE TABLE IF NOT EXISTS appPktSeq ("
        "timeSec DOUBLE  NOT NULL, "
        "txRx    TEXT    NOT NULL, "
        "nodeId  INTEGER NOT NULL, "
        "dstPort INTEGER NOT NULL, "
        "seq     INTEGER NOT NULL"
        ");");

    // relaySelect: one row per elected relay decision (real-time handoff log).
    db.SpinExec(
        "CREATE TABLE IF NOT EXISTS relaySelect ("
        "timeSec   DOUBLE  NOT NULL, "
        "boundaryM DOUBLE  NOT NULL, "
        "nodeId    INTEGER NOT NULL, "
        "distToTxM DOUBLE  NOT NULL, "
        "seq       INTEGER NOT NULL, "
        "forwarded INTEGER NOT NULL"
        ");");

    // relayTiming: one row per leader packet forwarded by the FIXED relay.
    // Captures the app-visible timestamps; the analysis joins psschTxUeMac /
    // psschRxUePhy / appPktSeq to recover hop1/hop2 (re)tx counts, PHY decode
    // time, the dynamic relay remaining-PDB, and end-to-end violations.
    db.SpinExec(
        "CREATE TABLE IF NOT EXISTS relayTiming ("
        "seq            INTEGER NOT NULL, "
        "origPort       INTEGER NOT NULL, "
        "relayNodeId    INTEGER NOT NULL, "
        "leaderGenSec   DOUBLE  NOT NULL, "
        "relayDecodeSec DOUBLE  NOT NULL, "
        "forwardSendSec DOUBLE  NOT NULL, "
        "forwarded      INTEGER NOT NULL"
        ");");

    // rlcFrag: estimated per-SDU fragmentation count, before vs after
    // compression. Populated by the controller's TX log callback.
    db.SpinExec(
        "CREATE TABLE IF NOT EXISTS rlcFrag ("
        "timeSec       DOUBLE  NOT NULL, "
        "txNodeId      INTEGER NOT NULL, "
        "pktSeq        INTEGER NOT NULL, "
        "origSdu       INTEGER NOT NULL, "
        "wireBytes     INTEGER NOT NULL, "
        "nSegmentsPre  INTEGER NOT NULL, "
        "nSegmentsPost INTEGER NOT NULL, "
        "tbSize        INTEGER NOT NULL"
        ");");
}

// ---------------------------------------------------------------------------
// KpiHooks::WriteAdaptLog
// ---------------------------------------------------------------------------

void
KpiHooks::WriteAdaptLog(double timeMs, double metric, uint8_t prevMcs, uint8_t newMcs,
                       const std::string& decision, uint32_t ackCount, uint32_t nackCount,
                       double windowStartMs, double windowEndMs)
{
    // Force log to appear
    std::cerr << "WriteAdaptLog CALLED: timeMs=" << timeMs << " metric=" << metric << std::endl;
    sqlite3_stmt* stmt;
    bool ok = db.WaitPrepare(&stmt, "INSERT INTO adaptLog (timeMs, metric, prevMcs, newMcs, decision, ackCount, nackCount, windowStartMs, windowEndMs) VALUES (?,?,?,?,?,?,?,?,?);");
    if (!ok || !stmt)
    {
        std::cerr << "WaitPrepare failed" << std::endl;
        return;
    }
    ok = db.Bind(stmt, 1, timeMs);
    if (!ok) { std::cerr << "Bind 1 failed" << std::endl; return; }
    ok = db.Bind(stmt, 2, metric);
    if (!ok) { std::cerr << "Bind 2 failed" << std::endl; return; }
    ok = db.Bind(stmt, 3, static_cast<uint32_t>(prevMcs));
    if (!ok) { std::cerr << "Bind 3 failed" << std::endl; return; }
    ok = db.Bind(stmt, 4, static_cast<uint32_t>(newMcs));
    if (!ok) { std::cerr << "Bind 4 failed" << std::endl; return; }
    ok = db.Bind(stmt, 5, decision);
    if (!ok) { std::cerr << "Bind 5 failed" << std::endl; return; }
    ok = db.Bind(stmt, 6, ackCount);
    if (!ok) { std::cerr << "Bind 6 failed" << std::endl; return; }
    ok = db.Bind(stmt, 7, nackCount);
    if (!ok) { std::cerr << "Bind 7 failed" << std::endl; return; }
    ok = db.Bind(stmt, 8, windowStartMs);
    if (!ok) { std::cerr << "Bind 8 failed" << std::endl; return; }
    ok = db.Bind(stmt, 9, windowEndMs);
    if (!ok) { std::cerr << "Bind 9 failed" << std::endl; return; }
    ok = db.SpinExec(stmt);
    if (!ok) { std::cerr << "SpinExec failed" << std::endl; return; }
    std::cerr << "WriteAdaptLog SUCCESS" << std::endl;
}

// ---------------------------------------------------------------------------
// KpiHooks::WriteAppPktSeq / WriteRelaySelect
// ---------------------------------------------------------------------------

void
KpiHooks::WriteAppPktSeq(double timeSec,
                         const std::string& txRx,
                         uint32_t nodeId,
                         uint16_t dstPort,
                         uint32_t seq)
{
    sqlite3_stmt* stmt = nullptr;
    bool ok = db.WaitPrepare(&stmt, "INSERT INTO appPktSeq VALUES (?,?,?,?,?);");
    if (!ok || !stmt) return;
    if (!db.Bind(stmt, 1, timeSec))                         return;
    if (!db.Bind(stmt, 2, txRx))                            return;
    if (!db.Bind(stmt, 3, nodeId))                          return;
    if (!db.Bind(stmt, 4, static_cast<uint32_t>(dstPort)))  return;
    if (!db.Bind(stmt, 5, seq))                             return;
    db.SpinExec(stmt);
}

void
KpiHooks::WriteRelaySelect(double timeSec,
                           double boundaryM,
                           uint32_t nodeId,
                           double distToTxM,
                           uint32_t seq,
                           int forwarded)
{
    sqlite3_stmt* stmt = nullptr;
    bool ok = db.WaitPrepare(&stmt, "INSERT INTO relaySelect VALUES (?,?,?,?,?,?);");
    if (!ok || !stmt) return;
    if (!db.Bind(stmt, 1, timeSec))                            return;
    if (!db.Bind(stmt, 2, boundaryM))                          return;
    if (!db.Bind(stmt, 3, nodeId))                             return;
    if (!db.Bind(stmt, 4, distToTxM))                          return;
    if (!db.Bind(stmt, 5, seq))                                return;
    if (!db.Bind(stmt, 6, static_cast<uint32_t>(forwarded)))   return;
    db.SpinExec(stmt);
}

void
KpiHooks::WriteRelayTiming(uint32_t seq,
                           uint16_t origPort,
                           uint32_t relayNodeId,
                           double leaderGenSec,
                           double relayDecodeSec,
                           double forwardSendSec,
                           int forwarded)
{
    sqlite3_stmt* stmt = nullptr;
    bool ok = db.WaitPrepare(&stmt, "INSERT INTO relayTiming VALUES (?,?,?,?,?,?,?);");
    if (!ok || !stmt) return;
    if (!db.Bind(stmt, 1, seq))                                return;
    if (!db.Bind(stmt, 2, static_cast<uint32_t>(origPort)))    return;
    if (!db.Bind(stmt, 3, relayNodeId))                        return;
    if (!db.Bind(stmt, 4, leaderGenSec))                       return;
    if (!db.Bind(stmt, 5, relayDecodeSec))                     return;
    if (!db.Bind(stmt, 6, forwardSendSec))                     return;
    if (!db.Bind(stmt, 7, static_cast<uint32_t>(forwarded)))   return;
    db.SpinExec(stmt);
}

// ---------------------------------------------------------------------------
// KpiHooks::WriteCompressionLog
// ---------------------------------------------------------------------------

void
KpiHooks::WriteCompressionLog(double timeSec,
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
                              const std::string& reason)
{
    sqlite3_stmt* stmt = nullptr;
    bool ok = db.WaitPrepare(&stmt,
        "INSERT INTO compressionLog (timeSec, txRx, nodeId, flowId, pktSeq, "
        "codec, origSize, compSize, encodeUs, decodeUs, entropyEst, reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?);");
    if (!ok || !stmt) return;
    if (!db.Bind(stmt, 1, timeSec))                                return;
    if (!db.Bind(stmt, 2, txRx))                                   return;
    if (!db.Bind(stmt, 3, nodeId))                                 return;
    if (!db.Bind(stmt, 4, static_cast<uint32_t>(flowId)))          return;
    if (!db.Bind(stmt, 5, pktSeq))                                 return;
    if (!db.Bind(stmt, 6, static_cast<uint32_t>(codec)))           return;
    if (!db.Bind(stmt, 7, origSize))                               return;
    if (!db.Bind(stmt, 8, compSize))                               return;
    if (!db.Bind(stmt, 9, static_cast<uint32_t>(encodeUs)))        return;
    if (!db.Bind(stmt, 10, static_cast<uint32_t>(decodeUs)))       return;
    if (!db.Bind(stmt, 11, entropyEst))                            return;
    if (!db.Bind(stmt, 12, reason))                                return;
    db.SpinExec(stmt);
}

// ---------------------------------------------------------------------------
// KpiHooks::WriteRlcFragLog
// ---------------------------------------------------------------------------

void
KpiHooks::WriteRlcFragLog(double timeSec,
                          uint32_t txNodeId,
                          uint32_t pktSeq,
                          uint32_t origSdu,
                          uint32_t wireBytes,
                          uint32_t nSegmentsPre,
                          uint32_t nSegmentsPost,
                          uint32_t tbSize)
{
    sqlite3_stmt* stmt = nullptr;
    bool ok = db.WaitPrepare(&stmt,
        "INSERT INTO rlcFrag (timeSec, txNodeId, pktSeq, origSdu, wireBytes, "
        "nSegmentsPre, nSegmentsPost, tbSize) VALUES (?,?,?,?,?,?,?,?);");
    if (!ok || !stmt) return;
    if (!db.Bind(stmt, 1, timeSec))      return;
    if (!db.Bind(stmt, 2, txNodeId))     return;
    if (!db.Bind(stmt, 3, pktSeq))       return;
    if (!db.Bind(stmt, 4, origSdu))      return;
    if (!db.Bind(stmt, 5, wireBytes))    return;
    if (!db.Bind(stmt, 6, nSegmentsPre)) return;
    if (!db.Bind(stmt, 7, nSegmentsPost))return;
    if (!db.Bind(stmt, 8, tbSize))       return;
    db.SpinExec(stmt);
}

// ---------------------------------------------------------------------------
// KpiHooks::RecentNackRate
// ---------------------------------------------------------------------------

double
KpiHooks::RecentNackRate(double windowMs) const
{
    return slHarq.GetWindowedNackRate(Simulator::Now().GetSeconds(), windowMs);
}

// ---------------------------------------------------------------------------
// ConnectPhyMacTraces
// ---------------------------------------------------------------------------

void
ConnectPhyMacTraces(KpiHooks& h)
{
    // PSCCH MAC TX
    Config::ConnectWithoutContext(
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/"
        "ComponentCarrierMapUe/*/NrUeMac/SlPscchScheduling",
        MakeBoundCallback(&OnSlPscchScheduling, &h.pscchTxMac));

    // PSSCH MAC TX
    Config::ConnectWithoutContext(
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/"
        "ComponentCarrierMapUe/*/NrUeMac/SlPsschScheduling",
        MakeBoundCallback(&OnSlPsschScheduling, &h.psschTxMac));

    // PSCCH PHY RX
    Config::ConnectWithoutContext(
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/"
        "ComponentCarrierMapUe/*/NrUePhy/SpectrumPhy/RxPscchTraceUe",
        MakeBoundCallback(&OnSlPscchRx, &h.pscchRxPhy));

    // PSSCH PHY RX  (also drives the optional per-RX SINR sink)
    Config::ConnectWithoutContext(
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/"
        "ComponentCarrierMapUe/*/NrUePhy/SpectrumPhy/RxPsschTraceUe",
        MakeBoundCallback(&OnSlPsschRx, &h.psschRxPhy, &h.sinrSink));

    // RLC RX PDU
    Config::ConnectWithoutContext(
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/"
        "ComponentCarrierMapUe/*/NrUeMac/RxRlcPduWithTxRnti",
        MakeBoundCallback(&OnSlRlcPduRx, &h.rlcRx));

    // PSFCH HARQ feedback (ACK/NACK) — context carries /NodeList/<id>/...
    Config::Connect(
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/ComponentCarrierMapUe/*/"
        "$ns3::BandwidthPartUe/NrUeMac/$ns3::NrSlUeMac/NrSlUeMacHarq/RxHarqFeedback",
        MakeBoundCallback(&OnSlHarqFeedback, &h.slHarq));

    // HARQ process timeout (TB exhausted retries without ACK)
    Config::Connect(
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/ComponentCarrierMapUe/*/"
        "$ns3::BandwidthPartUe/NrUeMac/$ns3::NrSlUeMac/NrSlUeMacHarq/Timeout",
        MakeBoundCallback(&OnSlHarqTimeout, &h.slHarq));
}

// ---------------------------------------------------------------------------
// ConnectAppTraces
// ---------------------------------------------------------------------------

void
ConnectAppTraces(KpiHooks& h, const FlowResult& flows)
{
    for (size_t f = 0; f < flows.defs.size(); ++f)
    {
        const uint16_t flowId = flows.defs[f].port;

        // TX (OnOff) apps
        for (uint32_t a = 0; a < flows.clients[f].GetN(); ++a)
        {
            Ptr<Application> app = flows.clients[f].Get(a);
            Ipv4Address localAddrs =
                app->GetNode()->GetObject<Ipv4L3Protocol>()->GetAddress(1, 0).GetLocal();

            app->TraceConnect("TxWithSeqTsSize",
                              "tx",
                              MakeBoundCallback(&OnUePacket,
                                               &h,
                                               &h.pktTxRx,
                                               &h.pktCsv,
                                               app->GetNode(),
                                               localAddrs,
                                               flowId));
        }

        // RX (PacketSink) apps
        for (uint32_t a = 0; a < flows.servers[f].GetN(); ++a)
        {
            Ptr<Application> app = flows.servers[f].Get(a);
            Ipv4Address localAddrs =
                app->GetNode()->GetObject<Ipv4L3Protocol>()->GetAddress(1, 0).GetLocal();

            app->TraceConnect("RxWithSeqTsSize",
                              "rx",
                              MakeBoundCallback(&OnUePacket,
                                               &h,
                                               &h.pktTxRx,
                                               &h.pktCsv,
                                               app->GetNode(),
                                               localAddrs,
                                               flowId));
        }
    }
}

// ---------------------------------------------------------------------------
// Flush
// ---------------------------------------------------------------------------

void
Flush(KpiHooks& h)
{
    // Drain in-memory caches → DB.
    h.pktTxRx.EmptyCache();
    h.pscchTxMac.EmptyCache();
    h.psschTxMac.EmptyCache();
    h.pscchRxPhy.EmptyCache();
    h.psschRxPhy.EmptyCache();
    h.rlcRx.EmptyCache();
    h.slHarq.EmptyCache();

    // Record each UE's IP→position mapping so V2xKpi can apply the range gate.
    for (auto it = NodeList::Begin(); it != NodeList::End(); ++it)
    {
        Ptr<Node> node = *it;
        for (uint32_t j = 0; j < node->GetNDevices(); ++j)
        {
            if (!node->GetDevice(j)->GetObject<NrUeNetDevice>())
            {
                continue;
            }
            std::ostringstream ip;
            ip << node->GetObject<Ipv4L3Protocol>()->GetAddress(1, 0).GetLocal();
            Vector pos = node->GetObject<MobilityModel>()->GetPosition();
            h.v2xKpi.FillPosPerIpMap(ip.str(), pos);
        }
    }

    h.v2xKpi.WriteKpis();

    if (h.pktCsv.open)
    {
        h.pktCsv.out.close();
    }
}

} // namespace ns3
