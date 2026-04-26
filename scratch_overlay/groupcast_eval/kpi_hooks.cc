// kpi_hooks.cc

#include "kpi_hooks.h"

#include "ns3/ipv4-l3-protocol.h"
#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/node-list.h"

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

// PSSCH PHY RX → saves TB decode outcome (success/BLER) and SINR.
static void
OnSlPsschRx(UePhyPsschRxOutputStats* s, const SlRxDataPacketTraceParams p)
{
    s->Save(p);
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

    // PSSCH PHY RX
    Config::ConnectWithoutContext(
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/"
        "ComponentCarrierMapUe/*/NrUePhy/SpectrumPhy/RxPsschTraceUe",
        MakeBoundCallback(&OnSlPsschRx, &h.psschRxPhy));

    // RLC RX PDU
    Config::ConnectWithoutContext(
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/"
        "ComponentCarrierMapUe/*/NrUeMac/RxRlcPduWithTxRnti",
        MakeBoundCallback(&OnSlRlcPduRx, &h.rlcRx));
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
