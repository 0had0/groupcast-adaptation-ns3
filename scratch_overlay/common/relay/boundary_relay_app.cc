// relay_app.cc — see relay_app.h for the design summary.

#include "common/relay/boundary_relay_app.h"

#include "ns3/applications-module.h"
#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/nr-module.h"
#include "ns3/udp-socket-factory.h"

#include <limits>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("BoundaryRelayApp");
NS_OBJECT_ENSURE_REGISTERED(BoundaryRelayApp);

TypeId
BoundaryRelayApp::GetTypeId()
{
    static TypeId tid = TypeId("ns3::BoundaryRelayApp")
                            .SetParent<Application>()
                            .SetGroupName("Applications")
                            .AddConstructor<BoundaryRelayApp>();
    return tid;
}

BoundaryRelayApp::BoundaryRelayApp() = default;
BoundaryRelayApp::~BoundaryRelayApp() = default;

void
BoundaryRelayApp::Configure(NodeContainer candidates,
                    uint32_t txNodeId,
                    Ipv4Address groupAddr,
                    uint16_t relayPort,
                    double boundaryM,
                    Time pdb)
{
    m_candidates = candidates;
    m_txNode = candidates.Get(txNodeId);
    m_groupAddr = groupAddr;
    m_relayPort = relayPort;
    m_boundaryM = boundaryM;
    m_pdb = pdb;
}

void
BoundaryRelayApp::StartApplication()
{
    if (!m_socket)
    {
        m_socket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
        m_socket->Bind();
        m_socket->Connect(InetSocketAddress(m_groupAddr, m_relayPort));
        m_socket->SetAllowBroadcast(true);
        m_socket->ShutdownRecv(); // TX-only socket
    }
}

void
BoundaryRelayApp::StopApplication()
{
    if (m_socket)
    {
        m_socket->Close();
        m_socket = nullptr;
    }
}

double
BoundaryRelayApp::DistToTx(Ptr<Node> n) const
{
    Ptr<MobilityModel> a = n->GetObject<MobilityModel>();
    Ptr<MobilityModel> b = m_txNode->GetObject<MobilityModel>();
    if (!a || !b)
        return std::numeric_limits<double>::max();
    return CalculateDistance(a->GetPosition(), b->GetPosition());
}

bool
BoundaryRelayApp::AmIElected() const
{
    // Winner = candidate minimising |dist_to_TX - boundary|, ties broken by the
    // lowest node id. Strict-closest: only that single node may relay.
    const uint32_t myId = GetNode()->GetId();
    double bestMetric = std::numeric_limits<double>::max();
    uint32_t bestId = std::numeric_limits<uint32_t>::max();
    for (uint32_t i = 0; i < m_candidates.GetN(); ++i)
    {
        Ptr<Node> n = m_candidates.Get(i);
        if (n->GetId() == m_txNode->GetId())
            continue; // the leader cannot relay itself
        const double metric = std::abs(DistToTx(n) - m_boundaryM);
        if (metric < bestMetric - 1e-9 ||
            (std::abs(metric - bestMetric) <= 1e-9 && n->GetId() < bestId))
        {
            bestMetric = metric;
            bestId = n->GetId();
        }
    }
    return bestId == myId;
}

void
BoundaryRelayApp::OnLeaderRx(Ptr<const Packet> p,
                     const Address& /*src*/,
                     const Address& /*dst*/,
                     const SeqTsSizeHeader& hdr)
{
    if (!AmIElected())
        return; // not the elected relay for this boundary right now

    const double now = Simulator::Now().GetSeconds();
    const uint32_t seq = hdr.GetSeq();
    const double myDist = DistToTx(GetNode());

    // PDB gate: the relayed copy is only useful if it can still beat the
    // original packet's delay budget. hdr.GetTs() is the leader's generation
    // time (this is the leader flow's header).
    const Time age = Simulator::Now() - hdr.GetTs();
    if (age >= m_pdb)
    {
        if (m_logCb)
            m_logCb(now, m_boundaryM, GetNode()->GetId(), myDist, seq, 0);
        return;
    }

    // Reconstruct an OnOff-equivalent packet so the far PacketSink (which has
    // EnableSeqTsSizeHeader=true) parses the SAME app sequence number. ts in
    // the new header is the relay send time and is intentionally unused — the
    // analysis derives end-to-end age from the leader's appPktSeq tx row.
    SeqTsSizeHeader out;
    out.SetSeq(seq);
    out.SetSize(hdr.GetSize());
    const uint32_t payload =
        (hdr.GetSize() > out.GetSerializedSize()) ? (hdr.GetSize() - out.GetSerializedSize()) : 0;
    Ptr<Packet> pkt = Create<Packet>(payload);
    pkt->AddHeader(out);
    m_socket->Send(pkt);

    if (m_logCb)
        m_logCb(now, m_boundaryM, GetNode()->GetId(), myDist, seq, 1);
}

// ---------------------------------------------------------------------------
// InstallRelayGroup
// ---------------------------------------------------------------------------

std::vector<Ptr<BoundaryRelayApp>>
InstallRelayGroup(const NodeContainer& ueNodes,
                  const SlStackCtx& ctx,
                  FlowResult& flows,
                  uint32_t txNodeId,
                  const ApplicationContainer& leaderSinks,
                  uint16_t leaderPort,
                  uint16_t relayPort,
                  uint32_t relayDstL2Id,
                  double boundaryM,
                  Time pdb,
                  double simTime)
{
    Ipv4Address groupAddr("225.63.63.1");
    const Time bearerActivation = Seconds(2.0);

    // 1. Broadcast relay bearer (harq disabled → no PSFCH → no NACK storm),
    //    dynamic grant for low-latency forwarding, on ALL UE devices so any
    //    elected follower can transmit and every UE can receive.
    SidelinkInfo slInfo;
    slInfo.m_castType = SidelinkInfo::CastType::Broadcast;
    slInfo.m_dstL2Id = relayDstL2Id;
    slInfo.m_rri = Time(0);
    slInfo.m_dynamic = true;
    slInfo.m_pdb = pdb;
    slInfo.m_harqEnabled = false;
    slInfo.m_priority = 1;

    Ptr<LteSlTft> tft = Create<LteSlTft>(LteSlTft::Direction::BIDIRECTIONAL,
                                         groupAddr,
                                         relayPort,
                                         slInfo);
    ctx.sl->ActivateNrSlBearer(bearerActivation, ctx.ueNetDev, tft);

    // 2. RX sinks on every UE (cooperative union reception). Appended to the
    //    FlowResult so ConnectAppTraces logs them under relayPort.
    PacketSinkHelper sink("ns3::UdpSocketFactory",
                          InetSocketAddress(Ipv4Address::GetAny(), relayPort));
    sink.SetAttribute("EnableSeqTsSizeHeader", BooleanValue(true));
    ApplicationContainer relaySinks;
    for (uint32_t i = 0; i < ueNodes.GetN(); ++i)
    {
        ApplicationContainer s = sink.Install(ueNodes.Get(i));
        s.Get(0)->SetStartTime(Seconds(0.0));
        relaySinks.Add(s);
    }

    // 3. BoundaryRelayApp on every follower, wired to that follower's leader sink.
    std::vector<Ptr<BoundaryRelayApp>> apps;
    for (uint32_t a = 0; a < leaderSinks.GetN(); ++a)
    {
        Ptr<Application> leaderSink = leaderSinks.Get(a);
        Ptr<Node> node = leaderSink->GetNode();
        if (node->GetId() == txNodeId)
            continue; // leader does not relay

        Ptr<BoundaryRelayApp> app = CreateObject<BoundaryRelayApp>();
        app->Configure(ueNodes, txNodeId, groupAddr, relayPort, boundaryM, pdb);
        node->AddApplication(app);
        app->SetStartTime(Seconds(0.0));
        app->SetStopTime(Seconds(bearerActivation.GetSeconds() + simTime + 1.0));
        leaderSink->TraceConnectWithoutContext(
            "RxWithSeqTsSize", MakeCallback(&BoundaryRelayApp::OnLeaderRx, app));
        apps.push_back(app);
    }

    // 4. Register the relay link as a pseudo-flow so kpi_hooks logs its sinks.
    FlowDef relayDef;
    relayDef.label = "relay" + std::to_string(static_cast<int>(boundaryM));
    relayDef.port = relayPort;
    relayDef.dstL2Id = relayDstL2Id;
    relayDef.enabled = true;
    flows.defs.push_back(relayDef);
    flows.clients.push_back(ApplicationContainer()); // no periodic TX app
    flows.servers.push_back(relaySinks);
    flows.appStart.push_back(bearerActivation.GetSeconds());
    flows.appStop.push_back(bearerActivation.GetSeconds() + simTime);

    return apps;
}

} // namespace ns3
