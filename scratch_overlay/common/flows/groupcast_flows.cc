// groupcast_flows.cc

#include "groupcast_flows.h"

#include "common/mcd/mcr_feedback_gate.h"
#include "common/traffic/custom-traffic-helper.h"

#include "ns3/log.h"

#include <algorithm>
#include <cctype>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("GroupcastEval.GroupcastFlows");

// ---------------------------------------------------------------------------
// Default flow table
// ---------------------------------------------------------------------------

std::vector<FlowDef>
DefaultGroupcastFlows()
{
    return {
        // label        port  prio  rri                dynamic  pdb                rate    enabled harq  txNodes startTime
        {"urllc_cam",  8001, 1,    MilliSeconds(20),  true,    MilliSeconds(20),  200.0,  true,   -1,   {},     Time(0)},
        {"v2x_video",  8002, 2,    MilliSeconds(50),  false,   MilliSeconds(50),  1000.0, true,   -1,   {},     Time(0)},
        {"non_gbr",    8003, 3,    MilliSeconds(100), false,   MilliSeconds(100), 100.0,  true,   -1,   {},     Time(0)},
    };
}

// ---------------------------------------------------------------------------
// CLI parser: --flows="k=v;k=v,...,k=v;k=v"
// ---------------------------------------------------------------------------

namespace
{

std::string
Trim(const std::string& s)
{
    size_t b = 0;
    size_t e = s.size();
    while (b < e && std::isspace(static_cast<unsigned char>(s[b])))
    {
        ++b;
    }
    while (e > b && std::isspace(static_cast<unsigned char>(s[e - 1])))
    {
        --e;
    }
    return s.substr(b, e - b);
}

std::vector<std::string>
Split(const std::string& s, char sep)
{
    std::vector<std::string> out;
    std::string buf;
    for (char c : s)
    {
        if (c == sep)
        {
            out.push_back(buf);
            buf.clear();
        }
        else
        {
            buf.push_back(c);
        }
    }
    out.push_back(buf);
    return out;
}

bool
ParseBool(const std::string& v, bool& out)
{
    std::string t = Trim(v);
    if (t == "1" || t == "true" || t == "yes")
    {
        out = true;
        return true;
    }
    if (t == "0" || t == "false" || t == "no")
    {
        out = false;
        return true;
    }
    return false;
}

} // namespace

Time
DefaultRriForTrafficClass(TrafficClass tc)
{
    switch (tc)
    {
        case TrafficClass::Periodic1:  return MilliSeconds(50);
        case TrafficClass::Periodic2:  return MilliSeconds(10); // 5ms unsupported by pool-factory enum
        case TrafficClass::Periodic3:  return MilliSeconds(10);
        case TrafficClass::Aperiodic1: return MilliSeconds(0);
        case TrafficClass::Aperiodic2: return MilliSeconds(0);
        case TrafficClass::OnOffConstant:
        default:                       return MilliSeconds(100);
    }
}

std::vector<FlowDef>
ParseFlowsSpec(const std::string& spec)
{
    std::vector<FlowDef> flows;
    std::set<uint16_t> seenPorts;

    auto specs = Split(spec, ',');
    for (size_t fi = 0; fi < specs.size(); ++fi)
    {
        std::string flowSpec = Trim(specs[fi]);
        if (flowSpec.empty())
        {
            continue;
        }

        FlowDef fd{};
        fd.port = 0; // sentinel: auto-assign if still 0 after parse
        fd.priority = 3;
        fd.rri = MilliSeconds(100); // fallback; overridden by traffic= default or explicit rri_ms
        fd.pdb = Time(0); // sentinel: default to rri after parse
        fd.dynamic = false;
        fd.rateKbps = 100.0;
        fd.enabled = true;
        fd.harqOverride = -1;
        fd.startTime = Time(0); // sentinel: use bearerActivation default
        fd.stopTime = Time(0);  // sentinel: appStart + simTime

        bool pdbSet      = false;
        bool rriExplicit = false;  // true once rri_ms=<n> appears in the spec

        for (const std::string& kv : Split(flowSpec, ';'))
        {
            std::string pair = Trim(kv);
            if (pair.empty())
            {
                continue;
            }
            auto eq = pair.find('=');
            if (eq == std::string::npos)
            {
                throw std::runtime_error("flow spec: missing '=' in '" + pair + "'");
            }
            std::string key = Trim(pair.substr(0, eq));
            std::string val = Trim(pair.substr(eq + 1));

            try
            {
                if (key == "label")
                {
                    fd.label = val;
                }
                else if (key == "port")
                {
                    int p = std::stoi(val);
                    if (p <= 0 || p > 65535)
                    {
                        throw std::runtime_error("port out of range: " + val);
                    }
                    fd.port = static_cast<uint16_t>(p);
                }
                else if (key == "prio")
                {
                    int p = std::stoi(val);
                    if (p < 1 || p > 8)
                    {
                        throw std::runtime_error("prio must be 1..8: " + val);
                    }
                    fd.priority = static_cast<uint8_t>(p);
                }
                else if (key == "rri_ms")
                {
                    auto r = static_cast<uint32_t>(std::stoul(val));
                    // 0 = DG sentinel; otherwise must be in the set that
                    // NrSlCommResourcePoolFactory accepts:
                    //   multiples of 10 in [10,100] or multiples of 50 in [150,1000].
                    // 250ms excluded: LteRrcSap::GetSlResoResvPrdValue has no MS250 case.
                    bool ok = (r == 0) ||
                              (r >= 10  && r <= 100  && r % 10 == 0) ||
                              (r >= 150 && r <= 1000 && r % 50 == 0 && r != 250);
                    if (!ok)
                    {
                        throw std::runtime_error(
                            "rri_ms must be 0, a multiple of 10 in [10,100], "
                            "or a multiple of 50 in [150,1000] (250 excluded): " + val);
                    }
                    fd.rri      = MilliSeconds(static_cast<int64_t>(r));
                    rriExplicit = true;
                }
                else if (key == "pdb_ms")
                {
                    double r = std::stod(val);
                    if (!(r > 0.0))
                    {
                        throw std::runtime_error("pdb_ms must be > 0: " + val);
                    }
                    // Fractional-ms preserved: at mu=1 a slot is 0.5 ms, so the
                    // dual-MCS scheme needs e.g. pdb_ms=9.5 (one slot less).
                    fd.pdb = Seconds(r / 1000.0);
                    pdbSet = true;
                }
                else if (key == "dynamic")
                {
                    if (!ParseBool(val, fd.dynamic))
                    {
                        throw std::runtime_error("dynamic must be 0/1: " + val);
                    }
                }
                else if (key == "rate_kbps")
                {
                    double r = std::stod(val);
                    if (!(r > 0.0))
                    {
                        throw std::runtime_error("rate_kbps must be > 0: " + val);
                    }
                    fd.rateKbps = r;
                }
                else if (key == "harq")
                {
                    std::string t = Trim(val);
                    if (t == "auto")
                    {
                        fd.harqOverride = -1;
                    }
                    else
                    {
                        bool b;
                        if (!ParseBool(t, b))
                        {
                            throw std::runtime_error("harq must be 0/1/auto: " + val);
                        }
                        fd.harqOverride = b ? 1 : 0;
                    }
                }
                else if (key == "enabled")
                {
                    if (!ParseBool(val, fd.enabled))
                    {
                        throw std::runtime_error("enabled must be 0/1: " + val);
                    }
                }
                else if (key == "tx_nodes")
                {
                    fd.txNodes.clear();
                    for (const std::string& tok : Split(val, ':'))
                    {
                        std::string t = Trim(tok);
                        if (t.empty())
                        {
                            continue;
                        }
                        int id = std::stoi(t);
                        if (id < 0)
                        {
                            throw std::runtime_error("tx_nodes id must be >= 0: " + t);
                        }
                        fd.txNodes.push_back(static_cast<uint32_t>(id));
                    }
                }
                else if (key == "start_ms")
                {
                    double s = std::stod(val);
                    if (!(s >= 0.0))
                    {
                        throw std::runtime_error("start_ms must be >= 0: " + val);
                    }
                    // Fractional-ms preserved (one-slot = 0.5 ms offset at mu=1).
                    fd.startTime = Seconds(s / 1000.0);
                }
                else if (key == "stop_ms")
                {
                    double s = std::stod(val);
                    if (!(s > 0.0))
                    {
                        throw std::runtime_error("stop_ms must be > 0: " + val);
                    }
                    fd.stopTime = MilliSeconds(static_cast<int64_t>(s));
                }
                else if (key == "traffic")
                {
                    static const std::map<std::string, TrafficClass> kMap = {
                        {"onoff",      TrafficClass::OnOffConstant},
                        {"periodic1",  TrafficClass::Periodic1},
                        {"periodic2",  TrafficClass::Periodic2},
                        {"periodic3",  TrafficClass::Periodic3},
                        {"aperiodic1", TrafficClass::Aperiodic1},
                        {"aperiodic2", TrafficClass::Aperiodic2},
                    };
                    auto it = kMap.find(Trim(val));
                    if (it == kMap.end())
                    {
                        throw std::runtime_error(
                            "traffic must be onoff|periodic1|periodic2|periodic3|"
                            "aperiodic1|aperiodic2: " + val);
                    }
                    fd.trafficClass = it->second;
                }
                else if (key == "mcs")
                {
                    int m = std::stoi(val);
                    if (m < 0 || m > 28)
                    {
                        throw std::runtime_error("mcs must be 0..28: " + val);
                    }
                    fd.mcs = static_cast<int16_t>(m);
                }
                else if (key == "dstL2Id")
                {
                    auto d = static_cast<uint32_t>(std::stoul(val));
                    fd.dstL2Id = d;
                }
                else if (key == "harq")
                {
                    static const std::map<std::string, HarqMode> kMap = {
                        {"default",  HarqMode::Default},
                        {"acknack",  HarqMode::AckNack},
                        {"nackonly", HarqMode::NackOnly},
                    };
                    auto it = kMap.find(Trim(val));
                    if (it == kMap.end())
                    {
                        throw std::runtime_error(
                            "harq must be default|acknack|nackonly: " + val);
                    }
                    fd.harqMode = it->second;
                }
                else if (key == "rxNodes")
                {
                    // Accepts colon-separated ids and/or ranges: "1:2:3" or
                    // "1-6" or "7-22" or mixed "1:3:5-10:12". Both - and :
                    // are valid separators at the top level; ranges use -.
                    fd.rxNodes.clear();
                    for (const std::string& tok : Split(val, ':'))
                    {
                        std::string t = Trim(tok);
                        if (t.empty())
                            continue;
                        // Check for range "lo-hi"
                        auto dash = t.find('-');
                        if (dash != std::string::npos)
                        {
                            int lo = std::stoi(t.substr(0, dash));
                            int hi = std::stoi(t.substr(dash + 1));
                            if (lo < 0 || hi < lo)
                                throw std::runtime_error("rxNodes range invalid: " + t);
                            for (int id = lo; id <= hi; ++id)
                                fd.rxNodes.push_back(static_cast<uint32_t>(id));
                        }
                        else
                        {
                            int id = std::stoi(t);
                            if (id < 0)
                                throw std::runtime_error("rxNodes id must be >= 0: " + t);
                            fd.rxNodes.push_back(static_cast<uint32_t>(id));
                        }
                    }
                }
                else
                {
                    throw std::runtime_error("unknown key '" + key + "'");
                }
            }
            catch (const std::invalid_argument&)
            {
                throw std::runtime_error("bad numeric value for '" + key + "': " + val);
            }
            catch (const std::out_of_range&)
            {
                throw std::runtime_error("numeric out of range for '" + key + "': " + val);
            }
        }

        if (fd.label.empty())
        {
            std::ostringstream os;
            os << "flow " << (fi + 1) << ": missing required key 'label'";
            throw std::runtime_error(os.str());
        }
        if (fd.port == 0)
        {
            fd.port = static_cast<uint16_t>(8000 + flows.size() + 1);
        }
        // RRI precedence: explicit rri_ms> per-class default> 100 ms fallback.
        if (!rriExplicit && fd.trafficClass != TrafficClass::OnOffConstant)
        {
            fd.rri = DefaultRriForTrafficClass(fd.trafficClass);
        }
        if (!pdbSet)
        {
            // pdb defaults to rri, but rri=0 (dynamic) would crash V2xKpi /
            // delay-budget logic — fall back to the historical 100 ms.
            fd.pdb = (fd.rri > Time(0)) ? fd.rri : MilliSeconds(100);
        }
        if (!seenPorts.insert(fd.port).second)
        {
            throw std::runtime_error("duplicate port in --flows: " +
                                     std::to_string(fd.port));
        }

        flows.push_back(std::move(fd));
    }

    bool anyEnabled = false;
    for (const auto& f : flows)
    {
        if (f.enabled)
        {
            anyEnabled = true;
            break;
        }
    }
    if (!anyEnabled)
    {
        throw std::runtime_error("--flows: no enabled flows after parsing");
    }

    return flows;
}

// ---------------------------------------------------------------------------
// Helper: bytes to fill one packet at (rateKbps, rri).
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
                      const std::vector<FlowDef>& flows,
                      double simTime,
                      bool harqEnabledDefault,
                      int64_t& stream)
{
    // groupcast destination L2 ID per 3GPP TS 38.321 (default 254). Each
    // FlowDef may override this via fd.dstL2Id to create independent groupcast
    // links from the same TX UE — see scratch_overlay/dual_groupcast_eval/.
    Ipv4Address groupAddr("225.63.63.1");
    const Time bearerActivation = Seconds(2.0);

    Ptr<UniformRandomVariable> jitterRv = CreateObject<UniformRandomVariable>();
    jitterRv->SetStream(stream++);
    jitterRv->SetAttribute("Min", DoubleValue(0.0));
    jitterRv->SetAttribute("Max", DoubleValue(0.05));

    FlowResult result;
    for (const auto& fd : flows)
    {
        if (!fd.enabled)
        {
            continue;
        }

        const bool harqEnabled =
            (fd.harqOverride < 0) ? harqEnabledDefault : (fd.harqOverride == 1);

        SidelinkInfo slInfo;
        // Additive: when the flow requests HARQ Option-1 (NACK-only), force
        // castType=GroupcastNegativeOnly and HARQ on. The PHY-level ACK-drop
        // behavior (option-1 silent-on-success) is enforced by
        // common/mcd/mcr_nack_only_gate installed at the simulation level.
        // All callers that don't set fd.harqMode keep the legacy behavior.
        if (fd.harqMode == HarqMode::NackOnly)
        {
            slInfo.m_castType = SidelinkInfo::CastType::GroupcastNegativeOnly;
            slInfo.m_harqEnabled = true;
        }
        else
        {
            slInfo.m_castType =
                (harqEnabled) ? SidelinkInfo::CastType::Groupcast
                              : SidelinkInfo::CastType::Broadcast;
            slInfo.m_harqEnabled = harqEnabled;
        }
        slInfo.m_dstL2Id = fd.dstL2Id;
        slInfo.m_rri = fd.rri;
        slInfo.m_dynamic = fd.dynamic;
        slInfo.m_pdb = fd.pdb;
        slInfo.m_priority = fd.priority;

        // Register the castType for this dstL2Id so the MCR feedback gate
        // shim can apply the per-castType rule (Broadcast=no feedback,
        // GroupcastOnlyNack=MCR gate, Groupcast=no gate, Unicast=no gate).
        // See common/mcd/mcr_feedback_gate.h for the registry contract.
        {
            uint8_t gateCastType = 1; // default = Groupcast (Option-2 ACK/NACK)
            if (slInfo.m_castType == SidelinkInfo::CastType::Broadcast)
            {
                gateCastType = 0;
            }
            else if (slInfo.m_castType == SidelinkInfo::CastType::GroupcastNegativeOnly)
            {
                gateCastType = 3;
            }
            else if (slInfo.m_castType == SidelinkInfo::CastType::Groupcast)
            {
                gateCastType = 1;
            }
            else if (slInfo.m_castType == SidelinkInfo::CastType::Unicast)
            {
                gateCastType = 2;
            }
            RegisterDstL2IdCastType(fd.dstL2Id, gateCastType);
        }

        Ptr<LteSlTft> tft = Create<LteSlTft>(LteSlTft::Direction::BIDIRECTIONAL,
                                             groupAddr,
                                             fd.port,
                                             slInfo);

        // Activate the SL bearer on the correct node set:
        //
        //   rxNodes non-empty → EXPLICIT partition. Activate only on
        //   txNodes ∪ rxNodes so non-member UEs never see the PSCCH, never
        //   decode the PSSCH, and never send PSFCH. This is the key fix for
        //   the dual-MCS scheme: far nodes must not participate in the
        //   high-MCS groupcast's HARQ loop or they will NACK it constantly.
        //
        //   rxNodes empty → OPEN group (legacy behaviour). All UEs join the
        //   groupcast group. txNodes only controls which nodes run the OnOff
        //   TX app, not who participates at the MAC layer.
        //
        // NOTE: txNodes empty + rxNodes non-empty is valid — it means the TX
        // is determined by the allTx path below (all nodes TX). The bearer
        // is activated on all rxNodes; TX-capable nodes should be included in
        // rxNodes by the caller in that case, or the TX nodes won't have the
        // bearer to transmit. For the common dual-MCS pattern (tx_nodes=0 +
        // rxNodes=<subset>) both sets are non-empty and the union is correct.
        if (!fd.rxNodes.empty())
        {
            // Union of explicit TX nodes + explicit RX nodes.
            std::set<uint32_t> bearerNodes(fd.txNodes.begin(), fd.txNodes.end());
            bearerNodes.insert(fd.rxNodes.begin(), fd.rxNodes.end());
            NetDeviceContainer flowDevs;
            for (uint32_t id : bearerNodes)
            {
                if (id < ctx.ueNetDev.GetN())
                    flowDevs.Add(ctx.ueNetDev.Get(id));
            }
            ctx.sl->ActivateNrSlBearer(bearerActivation, flowDevs, tft);
        }
        else
        {
            // Open group: activate on all UEs (original behaviour).
            ctx.sl->ActivateNrSlBearer(bearerActivation, ctx.ueNetDev, tft);
        }

        // Validate tx_nodes IDs against the actual node container.
        for (uint32_t id : fd.txNodes)
        {
            if (id >= nodes.GetN())
            {
                std::ostringstream os;
                os << "flow '" << fd.label << "': tx_nodes id " << id
                   << " >= numUEs (" << nodes.GetN() << ")";
                throw std::runtime_error(os.str());
            }
        }
        const std::set<uint32_t> txSet(fd.txNodes.begin(), fd.txNodes.end());
        const bool allTx = txSet.empty();

        // Validate rx_nodes IDs and build receive-set. Empty rxNodes preserves
        // today's behaviour (PacketSink on every UE so all nodes receive).
        for (uint32_t id : fd.rxNodes)
        {
            if (id >= nodes.GetN())
            {
                std::ostringstream os;
                os << "flow '" << fd.label << "': rx_nodes id " << id
                   << " >= numUEs (" << nodes.GetN() << ")";
                throw std::runtime_error(os.str());
            }
        }
        const std::set<uint32_t> rxSet(fd.rxNodes.begin(), fd.rxNodes.end());
        const bool allRx = rxSet.empty();

        // Validate start_ms (must be >= bearerActivation) and resolve it.
        if (fd.startTime > Time(0) && fd.startTime < bearerActivation)
        {
            std::ostringstream os;
            os << "flow '" << fd.label << "': start_ms ("
               << fd.startTime.GetMilliSeconds()
               << ") must be >= bearerActivation ("
               << bearerActivation.GetMilliSeconds() << ")";
            throw std::runtime_error(os.str());
        }
        const Time effectiveStart =
            (fd.startTime > Time(0)) ? fd.startTime : bearerActivation;

        // Validate stop_ms vs start_ms.
        if (fd.stopTime > Time(0) && fd.stopTime <= effectiveStart)
        {
            std::ostringstream os;
            os << "flow '" << fd.label << "': stop_ms ("
               << fd.stopTime.GetMilliSeconds()
               << ") must be > effective start ("
               << effectiveStart.GetMilliSeconds() << " ms)";
            throw std::runtime_error(os.str());
        }

        const uint32_t pktSize = PacketSize(fd.rateKbps, fd.rri);
        const std::string rateStr = std::to_string(fd.rateKbps) + "kb/s";
        const InetSocketAddress dest(groupAddr, fd.port);
        const bool isOnOff = (fd.trafficClass == TrafficClass::OnOffConstant);

        // Build the TX helper. OnOffHelper drives constant-rate periodic UDP;
        // CustomTrafficHelper drives one of five 3GPP-V2X-style traffic modes
        // (periodic1..3, aperiodic1..2) — packet size and inter-arrival are
        // determined by the mode, so rateKbps / rri_ms become informational.
        ApplicationHelper* client = nullptr;
        if (isOnOff)
        {
            OnOffHelper* h = new OnOffHelper("ns3::UdpSocketFactory", dest);
            h->SetAttribute("EnableSeqTsSizeHeader", BooleanValue(true));
            h->SetConstantRate(DataRate(rateStr), pktSize);
            client = h;
        }
        else
        {
            CustomTrafficHelper* h = new CustomTrafficHelper("ns3::UdpSocketFactory", dest);
            // TrafficClass enum values 1..5 map to CustomTrafficApplication modes 0..4.
            h->SetTrafficMode(static_cast<int>(fd.trafficClass) - 1);
            client = h;
            NS_LOG_INFO("Flow '" << fd.label << "' uses CustomTraffic mode "
                        << (static_cast<int>(fd.trafficClass) - 1)
                        << " — rate_kbps/rri_ms are informational (ignored)");
        }

        PacketSinkHelper sink("ns3::UdpSocketFactory",
                              InetSocketAddress(Ipv4Address::GetAny(), fd.port));
        sink.SetAttribute("EnableSeqTsSizeHeader", BooleanValue(true));

        ApplicationContainer flowClients;
        ApplicationContainer flowServers;
        double earliestStart = 0.0;
        double latestStop = 0.0;

        for (uint32_t i = 0; i < nodes.GetN(); ++i)
        {
            // TX app: only on UEs in txNodes (or all if list is empty).
            if (allTx || txSet.count(i))
            {
                ApplicationContainer c = client->Install(nodes.Get(i));
                const double jitter = jitterRv->GetValue();
                c.Get(0)->SetStartTime(effectiveStart + Seconds(jitter));

                // For OnOff: account for first-packet send delay at constant rate.
                // For CustomTraffic: the mode itself schedules the first send, so
                // the effective start time IS the SetStartTime (no extra delay).
                const double firstPktDelay = isOnOff
                    ? static_cast<double>(pktSize) * 8.0 / DataRate(rateStr).GetBitRate()
                    : 0.0;
                const double appStart = effectiveStart.GetSeconds() + jitter + firstPktDelay;
                const double appStop = (fd.stopTime > Time(0))
                                           ? fd.stopTime.GetSeconds()
                                           : (appStart + simTime);
                c.Get(0)->SetStopTime(Seconds(appStop));

                earliestStart = std::max(earliestStart, appStart);
                latestStop = std::max(latestStop, appStop);
                flowClients.Add(c);
            }

            // PacketSink RX app: by default on every UE so all nodes receive.
            // When fd.rxNodes is non-empty (split groupcast), only those nodes
            // get a sink — partitioning the application-layer receiver set.
            if (allRx || rxSet.count(i))
            {
                ApplicationContainer s = sink.Install(nodes.Get(i));
                s.Get(0)->SetStartTime(Seconds(0.0));
                flowServers.Add(s);
            }
        }

        result.defs.push_back(fd);
        result.clients.push_back(flowClients);
        result.servers.push_back(flowServers);
        result.appStart.push_back(earliestStart);
        result.appStop.push_back(latestStop);

        const char* tcName = "onoff";
        switch (fd.trafficClass)
        {
            case TrafficClass::Periodic1:  tcName = "periodic1";  break;
            case TrafficClass::Periodic2:  tcName = "periodic2";  break;
            case TrafficClass::Periodic3:  tcName = "periodic3";  break;
            case TrafficClass::Aperiodic1: tcName = "aperiodic1"; break;
            case TrafficClass::Aperiodic2: tcName = "aperiodic2"; break;
            default: break;
        }
        std::ostringstream rxNodesStr;
        if (allRx)
        {
            rxNodesStr << "all";
        }
        else
        {
            bool first = true;
            for (uint32_t id : fd.rxNodes)
            {
                if (!first) rxNodesStr << ":";
                rxNodesStr << id;
                first = false;
            }
        }
        NS_LOG_INFO("Flow " << result.defs.size() << " (" << fd.label << "): port=" << fd.port
                            << " dstL2Id=" << fd.dstL2Id
                            << " prio=" << +fd.priority
                            << " rri=" << fd.rri.GetMilliSeconds() << "ms"
                            << " pdb=" << fd.pdb.GetMilliSeconds() << "ms"
                            << " traffic=" << tcName
                            << (isOnOff ? std::string(" pktSize=") + std::to_string(pktSize) + "B rate=" + rateStr : "")
                            << (fd.dynamic ? " DG" : " SPS")
                            << " harq=" << (harqEnabled ? 1 : 0)
                            << " rxNodes=" << rxNodesStr.str());
    }

    return result;
}

} // namespace ns3
