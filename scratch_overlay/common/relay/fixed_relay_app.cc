// relay_app.cc  (static_relay_collision_eval) — see relay_app.h for the design.

#include "common/relay/fixed_relay_app.h"

#include "ns3/applications-module.h"
#include "ns3/log.h"
#include "ns3/nr-module.h"
#include "ns3/udp-socket-factory.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("FixedRelayApp");
NS_OBJECT_ENSURE_REGISTERED(FixedRelayApp);

TypeId
FixedRelayApp::GetTypeId()
{
    static TypeId tid = TypeId("ns3::FixedRelayApp")
                            .SetParent<Application>()
                            .SetGroupName("Applications")
                            .AddConstructor<FixedRelayApp>();
    return tid;
}

FixedRelayApp::FixedRelayApp() = default;
FixedRelayApp::~FixedRelayApp() = default;

void
FixedRelayApp::Configure(Ipv4Address groupAddr, uint16_t relayPort, uint16_t leaderPort)
{
    m_groupAddr = groupAddr;
    m_relayPort = relayPort;
    m_leaderPort = leaderPort;
}

void
FixedRelayApp::StartApplication()
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
FixedRelayApp::StopApplication()
{
    if (m_socket)
    {
        m_socket->Close();
        m_socket = nullptr;
    }
}

void
FixedRelayApp::OnLeaderRx(Ptr<const Packet> /*p*/,
                     const Address& /*src*/,
                     const Address& /*dst*/,
                     const SeqTsSizeHeader& hdr)
{
    // Fixed relay: this app only exists on the relay node, so every decoded
    // leader packet is forwarded (no election, no PDB drop). We log the timing
    // so the analysis can compute the dynamic remaining budget and violations.
    const uint32_t seq = hdr.GetSeq();
    const double leaderGenSec = hdr.GetTs().GetSeconds();   // leader generation time
    const double relayDecodeSec = Simulator::Now().GetSeconds(); // app deliver = decode done

    // Reconstruct an OnOff-equivalent packet so the tail PacketSink (which has
    // EnableSeqTsSizeHeader=true) parses the SAME app sequence number.
    SeqTsSizeHeader out;
    out.SetSeq(seq);
    out.SetSize(hdr.GetSize());
    const uint32_t payload =
        (hdr.GetSize() > out.GetSerializedSize()) ? (hdr.GetSize() - out.GetSerializedSize()) : 0;
    Ptr<Packet> pkt = Create<Packet>(payload);
    pkt->AddHeader(out);
    m_socket->Send(pkt);

    const double forwardSendSec = Simulator::Now().GetSeconds();
    if (m_timingCb)
        m_timingCb(seq, m_leaderPort, GetNode()->GetId(),
                   leaderGenSec, relayDecodeSec, forwardSendSec, 1);
}

// ---------------------------------------------------------------------------
// InstallFixedRelay
// ---------------------------------------------------------------------------

Ptr<FixedRelayApp>
InstallFixedRelay(const NodeContainer& ueNodes,
                  const SlStackCtx& ctx,
                  FlowResult& flows,
                  uint32_t relayNodeId,
                  const std::vector<uint32_t>& tailNodes,
                  Ptr<Application> relayLeaderSink,
                  uint16_t leaderPort,
                  uint16_t relayPort,
                  uint32_t relayDstL2Id,
                  Time relayPdb,
                  Time rri,
                  bool dynamic,
                  bool harqEnabled,
                  double simTime)
{
    Ipv4Address groupAddr("225.63.63.2"); // distinct multicast group from leader
    const Time bearerActivation = Seconds(2.0);

    // 1. Group-B bearer: groupcast (PSFCH ACK/NACK) by default, SAME SB-SPS
    //    config as the leader (rri/dynamic), only the PDB differs. Activated on
    //    {relay ∪ tail} so the relay can TX and the tail can RX/feedback.
    SidelinkInfo slInfo;
    slInfo.m_castType =
        harqEnabled ? SidelinkInfo::CastType::Groupcast : SidelinkInfo::CastType::Broadcast;
    slInfo.m_dstL2Id = relayDstL2Id;
    slInfo.m_rri = rri;
    slInfo.m_dynamic = dynamic;
    slInfo.m_pdb = relayPdb;
    slInfo.m_harqEnabled = harqEnabled;
    slInfo.m_priority = 1;

    Ptr<LteSlTft> tft =
        Create<LteSlTft>(LteSlTft::Direction::BIDIRECTIONAL, groupAddr, relayPort, slInfo);

    NetDeviceContainer grpDevs;
    grpDevs.Add(ctx.ueNetDev.Get(relayNodeId));
    for (uint32_t id : tailNodes)
        if (id < ctx.ueNetDev.GetN())
            grpDevs.Add(ctx.ueNetDev.Get(id));
    ctx.sl->ActivateNrSlBearer(bearerActivation, grpDevs, tft);

    // 2. RX sinks on the tail nodes only (strict partition). Appended to the
    //    FlowResult so ConnectAppTraces logs them under relayPort.
    PacketSinkHelper sink("ns3::UdpSocketFactory",
                          InetSocketAddress(Ipv4Address::GetAny(), relayPort));
    sink.SetAttribute("EnableSeqTsSizeHeader", BooleanValue(true));
    ApplicationContainer relaySinks;
    for (uint32_t id : tailNodes)
    {
        ApplicationContainer s = sink.Install(ueNodes.Get(id));
        s.Get(0)->SetStartTime(Seconds(0.0));
        relaySinks.Add(s);
    }

    // 3. One FixedRelayApp on the relay node, wired to its Group-A leader sink.
    Ptr<FixedRelayApp> app = CreateObject<FixedRelayApp>();
    app->Configure(groupAddr, relayPort, leaderPort);
    ueNodes.Get(relayNodeId)->AddApplication(app);
    app->SetStartTime(Seconds(0.0));
    app->SetStopTime(Seconds(bearerActivation.GetSeconds() + simTime + 1.0));
    relayLeaderSink->TraceConnectWithoutContext(
        "RxWithSeqTsSize", MakeCallback(&FixedRelayApp::OnLeaderRx, app));

    // 4. Register the relay link as a pseudo-flow so kpi_hooks logs its sinks.
    FlowDef relayDef;
    relayDef.label = "relayB";
    relayDef.port = relayPort;
    relayDef.dstL2Id = relayDstL2Id;
    relayDef.enabled = true;
    flows.defs.push_back(relayDef);
    flows.clients.push_back(ApplicationContainer()); // no periodic TX app
    flows.servers.push_back(relaySinks);
    flows.appStart.push_back(bearerActivation.GetSeconds());
    flows.appStop.push_back(bearerActivation.GetSeconds() + simTime);

    return app;
}

} // namespace ns3
