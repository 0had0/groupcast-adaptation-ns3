// common/relay/same_l2id_relay_app.cc
//
// See same_l2id_relay_app.h for design rationale.

#include "same_l2id_relay_app.h"

#include "ns3/applications-module.h"
#include "ns3/log.h"
#include "ns3/nr-module.h"
#include "ns3/udp-socket-factory.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("SameL2IdRelayApp");
NS_OBJECT_ENSURE_REGISTERED(SameL2IdRelayApp);

TypeId
SameL2IdRelayApp::GetTypeId()
{
    static TypeId tid = TypeId("ns3::SameL2IdRelayApp")
                            .SetParent<Application>()
                            .SetGroupName("Applications")
                            .AddConstructor<SameL2IdRelayApp>();
    return tid;
}

SameL2IdRelayApp::SameL2IdRelayApp() = default;
SameL2IdRelayApp::~SameL2IdRelayApp() = default;

void
SameL2IdRelayApp::Configure(Ipv4Address groupAddr, uint16_t port)
{
    m_groupAddr = groupAddr;
    m_port      = port;
}

void
SameL2IdRelayApp::StartApplication()
{
    if (!m_socket)
    {
        m_socket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
        m_socket->Bind();
        m_socket->Connect(InetSocketAddress(m_groupAddr, m_port));
        m_socket->SetAllowBroadcast(true);
        m_socket->ShutdownRecv(); // TX-only; reception is handled by PacketSink
    }
}

void
SameL2IdRelayApp::StopApplication()
{
    if (m_socket)
    {
        m_socket->Close();
        m_socket = nullptr;
    }
}

void
SameL2IdRelayApp::OnLeaderRx(Ptr<const Packet> /*p*/,
                              const Address& /*src*/,
                              const Address& /*dst*/,
                              const SeqTsSizeHeader& hdr)
{
    const uint32_t seq           = hdr.GetSeq();
    const double   leaderGenSec  = hdr.GetTs().GetSeconds();
    const double   decodeSec     = Simulator::Now().GetSeconds();

    // Rebuild a packet with the same seq/ts/size so that the tail nodes'
    // PacketSink (EnableSeqTsSizeHeader=true) can parse it correctly.
    SeqTsSizeHeader out;
    out.SetSeq(seq);
    out.SetSize(hdr.GetSize());
    const uint32_t headerBytes = out.GetSerializedSize();
    const uint32_t payloadBytes =
        (hdr.GetSize() > headerBytes) ? (hdr.GetSize() - headerBytes) : 0;
    Ptr<Packet> pkt = Create<Packet>(payloadBytes);
    pkt->AddHeader(out);

    if (m_socket)
        m_socket->Send(pkt);

    const double sendSec = Simulator::Now().GetSeconds();

    NS_LOG_INFO("SameL2IdRelay node " << GetNode()->GetId()
                << " fwd seq=" << seq
                << " leaderGen=" << leaderGenSec
                << " decode=" << decodeSec
                << " send=" << sendSec);

    if (m_timingCb)
        m_timingCb(seq, m_port, GetNode()->GetId(),
                   leaderGenSec, decodeSec, sendSec, 1);
}

// ---------------------------------------------------------------------------
// InstallSameL2IdRelay
// ---------------------------------------------------------------------------

Ptr<SameL2IdRelayApp>
InstallSameL2IdRelay(const NodeContainer& ueNodes,
                     const SlStackCtx& /*ctx*/,
                     const FlowResult& flows,
                     uint32_t relayNodeId,
                     uint32_t flowIdx,
                     double simTime)
{
    NS_ABORT_MSG_IF(relayNodeId >= ueNodes.GetN(),
                    "InstallSameL2IdRelay: relayNodeId=" << relayNodeId
                    << " >= numUEs=" << ueNodes.GetN());
    NS_ABORT_MSG_IF(flowIdx >= flows.defs.size(),
                    "InstallSameL2IdRelay: flowIdx=" << flowIdx
                    << " out of range (numFlows=" << flows.defs.size() << ")");

    const FlowDef& fd = flows.defs.at(flowIdx);

    // The leader groupcast address is always 225.63.63.1 (embedded in
    // groupcast_flows.cc; expose via the flow definition port for matching).
    Ipv4Address groupAddr("225.63.63.1");

    // Create and configure the relay app.
    Ptr<SameL2IdRelayApp> relayApp = CreateObject<SameL2IdRelayApp>();
    relayApp->Configure(groupAddr, fd.port);

    Ptr<Node> relayNode = ueNodes.Get(relayNodeId);
    relayNode->AddApplication(relayApp);
    relayApp->SetStartTime(Seconds(2.0)); // match bearer activation
    relayApp->SetStopTime(Seconds(2.0 + simTime + 1.0));

    // Wire: find the relay node's PacketSink for this flow and connect the
    // RxWithSeqTsSize trace to OnLeaderRx.
    // The server ApplicationContainer for flowIdx contains one PacketSink per
    // RX node; we iterate to find the one installed on relayNodeId.
    const ApplicationContainer& servers = flows.servers.at(flowIdx);
    bool wired = false;
    for (uint32_t i = 0; i < servers.GetN(); ++i)
    {
        Ptr<Application> app = servers.Get(i);
        if (app->GetNode()->GetId() != relayNode->GetId())
            continue;

        Ptr<PacketSink> sink = DynamicCast<PacketSink>(app);
        NS_ABORT_MSG_IF(!sink,
                        "InstallSameL2IdRelay: server app on relay node is not a PacketSink");
        sink->TraceConnectWithoutContext(
            "RxWithSeqTsSize",
            MakeCallback(&SameL2IdRelayApp::OnLeaderRx, relayApp));
        wired = true;
        NS_LOG_INFO("SameL2IdRelay: wired relay node " << relayNodeId
                    << " to PacketSink on flow '" << fd.label
                    << "' port=" << fd.port);
        break;
    }

    NS_ABORT_MSG_IF(!wired,
                    "InstallSameL2IdRelay: could not find PacketSink for relay node "
                    << relayNodeId << " on flow " << flowIdx
                    << ". Check that relayNodeId is in the flow's rxNodes (or rxNodes is empty).");

    return relayApp;
}

} // namespace ns3
