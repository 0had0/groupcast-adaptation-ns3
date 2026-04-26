// groupcast_flows.cc

#include "groupcast_flows.h"

#include "ns3/log.h"

#include <sstream>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("GroupcastEval.GroupcastFlows");

// ---------------------------------------------------------------------------
// Flow table — the single source of truth for all traffic profiles.
// ---------------------------------------------------------------------------

const FlowDef kFlows[kNumFlows] = {
    // port  prio  rri                dynamic  pdb                rateKbps  label
    {8001,   1,    MilliSeconds(20),  true,    MilliSeconds(20),  200.0,    "urllc_cam"},
    {8002,   2,    MilliSeconds(50),  false,   MilliSeconds(50),  1000.0,   "v2x_video"},
    {8003,   3,    MilliSeconds(100), false,   MilliSeconds(100), 100.0,    "non_gbr"},
};

// ---------------------------------------------------------------------------
// Helper: bytes to fill one packet at (rateKbps, rri).
// Copied verbatim from sl-multi-lc-example.cc:995.
// ---------------------------------------------------------------------------
static uint32_t
PacketSize(double rateKbps, Time rri)
{
    return static_cast<uint32_t>(rateKbps * 1000.0 * rri.GetSeconds() / 8.0);
}

// ---------------------------------------------------------------------------
// InstallGroupcastFlows
// ---------------------------------------------------------------------------

FlowResult
InstallGroupcastFlows(const NodeContainer& nodes,
                      const SlStackCtx& ctx,
                      double simTime,
                      bool harqEnabled,
                      int64_t& stream)
{
    const uint32_t dstL2Id = 254; // groupcast destination L2 ID per 3GPP TS 38.321
    Ipv4Address groupAddr("225.63.63.1");
    const Time bearerActivation = Seconds(2.0);

    // Small per-UE TX start jitter to avoid simultaneous first-packet bursts
    // (matches nr-v2x-west-to-east-highway.cc:1069-1072).
    Ptr<UniformRandomVariable> jitterRv = CreateObject<UniformRandomVariable>();
    jitterRv->SetStream(stream++);
    jitterRv->SetAttribute("Min", DoubleValue(0.0));
    jitterRv->SetAttribute("Max", DoubleValue(0.05)); // up to 50 ms jitter

    FlowResult result;

    for (int f = 0; f < kNumFlows; ++f)
    {
        const FlowDef& fd = kFlows[f];

        // -------------------------------------------------------------------
        // Sidelink bearer: one SidelinkInfo + one BIDIRECTIONAL LteSlTft per
        // flow. All three flows share the same dstL2Id (groupcast) and
        // multicast IP but are distinguished by port number.
        // Pattern: sl-multi-lc-example.cc:573-739
        // -------------------------------------------------------------------
        SidelinkInfo slInfo;
        slInfo.m_castType = SidelinkInfo::CastType::Groupcast;
        slInfo.m_dstL2Id = dstL2Id;
        slInfo.m_rri = fd.rri;
        slInfo.m_dynamic = fd.dynamic;
        slInfo.m_pdb = fd.pdb;
        slInfo.m_harqEnabled = harqEnabled;
        slInfo.m_priority = fd.priority;

        Ptr<LteSlTft> tft = Create<LteSlTft>(LteSlTft::Direction::BIDIRECTIONAL,
                                             groupAddr,
                                             fd.port,
                                             slInfo);
        ctx.sl->ActivateNrSlBearer(bearerActivation, ctx.ueNetDev, tft);

        // -------------------------------------------------------------------
        // Applications: OnOff (TX) + PacketSink (RX) on every UE.
        // Packet size is derived from (rate, RRI) so that one packet per RRI
        // saturates the bearer at exactly the requested rate.
        // Pattern: sl-multi-lc-example.cc:749-821
        // -------------------------------------------------------------------
        const uint32_t pktSize = PacketSize(fd.rateKbps, fd.rri);
        const std::string rateStr = std::to_string(fd.rateKbps) + "kb/s";

        OnOffHelper client("ns3::UdpSocketFactory", InetSocketAddress(groupAddr, fd.port));
        client.SetAttribute("EnableSeqTsSizeHeader", BooleanValue(true));
        client.SetConstantRate(DataRate(rateStr), pktSize);

        PacketSinkHelper sink("ns3::UdpSocketFactory",
                              InetSocketAddress(Ipv4Address::GetAny(), fd.port));
        sink.SetAttribute("EnableSeqTsSizeHeader", BooleanValue(true));

        for (uint32_t i = 0; i < nodes.GetN(); ++i)
        {
            // TX app
            ApplicationContainer c = client.Install(nodes.Get(i));
            const double jitter = jitterRv->GetValue();
            c.Get(0)->SetStartTime(bearerActivation + Seconds(jitter));

            const double firstPktDelay =
                static_cast<double>(pktSize) * 8.0 / DataRate(rateStr).GetBitRate();
            const double appStart = bearerActivation.GetSeconds() + jitter + firstPktDelay;
            const double appStop = appStart + simTime;
            c.Get(0)->SetStopTime(Seconds(appStop));

            result.appStart[f] = std::max(result.appStart[f], appStart);
            result.appStop[f] = std::max(result.appStop[f], appStop);
            result.clients[f].Add(c);

            // RX app (starts early so it is ready before the first packet arrives)
            ApplicationContainer s = sink.Install(nodes.Get(i));
            s.Get(0)->SetStartTime(Seconds(0.0));
            result.servers[f].Add(s);
        }

        NS_LOG_INFO("Flow " << f + 1 << " (" << fd.label << "): port=" << fd.port
                             << " prio=" << +fd.priority
                             << " rri=" << fd.rri.GetMilliSeconds() << "ms"
                             << " pktSize=" << pktSize << "B"
                             << " rate=" << rateStr
                             << (fd.dynamic ? " DG" : " SPS"));
    }

    return result;
}

} // namespace ns3
