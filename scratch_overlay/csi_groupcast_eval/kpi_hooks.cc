// kpi_hooks.cc

#include "kpi_hooks.h"

#include "ns3/abort.h"
#include "ns3/ipv4-l3-protocol.h"
#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/node-list.h"
#include "ns3/nr-phy-mac-common.h"

#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <sstream>

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
static void
OnUePacket(UeToUePktTxRxOutputStats* dbStats,
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
    const uint32_t pktSize = p->GetSize() + hdr.GetSerializedSize();

    dbStats->Save(txRx, localAddrs, nodeId, imsi, pktSize, srcAddrs, dstAddrs, seq);

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
            m_db->SpinPrepare(&stmt, "INSERT INTO slHarqFeedback VALUES (?,?,?,?,?,?,?);");
            NS_ABORT_UNLESS(m_db->Bind(stmt, 1, r.t));
            NS_ABORT_UNLESS(m_db->Bind(stmt, 2, r.nodeId));
            NS_ABORT_UNLESS(m_db->Bind(stmt, 3, static_cast<uint32_t>(r.rxRnti)));
            NS_ABORT_UNLESS(m_db->Bind(stmt, 4, static_cast<uint32_t>(r.txRnti)));
            NS_ABORT_UNLESS(m_db->Bind(stmt, 5, r.dstL2Id));
            NS_ABORT_UNLESS(m_db->Bind(stmt, 6, static_cast<uint32_t>(r.harqId)));
            NS_ABORT_UNLESS(m_db->Bind(stmt, 7, r.status));
            NS_ABORT_UNLESS(m_db->SpinExec(stmt));
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
            m_db->SpinPrepare(&stmt, "INSERT INTO slHarqTimeout VALUES (?,?,?);");
            NS_ABORT_UNLESS(m_db->Bind(stmt, 1, r.t));
            NS_ABORT_UNLESS(m_db->Bind(stmt, 2, r.nodeId));
            NS_ABORT_UNLESS(m_db->Bind(stmt, 3, static_cast<uint32_t>(r.harqId)));
            NS_ABORT_UNLESS(m_db->SpinExec(stmt));
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
