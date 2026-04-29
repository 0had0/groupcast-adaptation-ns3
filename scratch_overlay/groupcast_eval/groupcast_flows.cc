// groupcast_flows.cc

#include "groupcast_flows.h"

#include "ns3/log.h"

#include <algorithm>
#include <cctype>
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
        fd.rri = MilliSeconds(100);
        fd.pdb = Time(0); // sentinel: default to rri after parse
        fd.dynamic = false;
        fd.rateKbps = 100.0;
        fd.enabled = true;
        fd.harqOverride = -1;
        fd.startTime = Time(0); // sentinel: use bearerActivation default

        bool pdbSet = false;

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
                    double r = std::stod(val);
                    if (!(r > 0.0))
                    {
                        throw std::runtime_error("rri_ms must be > 0: " + val);
                    }
                    fd.rri = MilliSeconds(static_cast<int64_t>(r));
                }
                else if (key == "pdb_ms")
                {
                    double r = std::stod(val);
                    if (!(r > 0.0))
                    {
                        throw std::runtime_error("pdb_ms must be > 0: " + val);
                    }
                    fd.pdb = MilliSeconds(static_cast<int64_t>(r));
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
                    fd.startTime = MilliSeconds(static_cast<int64_t>(s));
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
        if (!pdbSet)
        {
            fd.pdb = fd.rri;
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
    const uint32_t dstL2Id = 254; // groupcast destination L2 ID per 3GPP TS 38.321
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

        const uint32_t pktSize = PacketSize(fd.rateKbps, fd.rri);
        const std::string rateStr = std::to_string(fd.rateKbps) + "kb/s";

        OnOffHelper client("ns3::UdpSocketFactory", InetSocketAddress(groupAddr, fd.port));
        client.SetAttribute("EnableSeqTsSizeHeader", BooleanValue(true));
        client.SetConstantRate(DataRate(rateStr), pktSize);

        PacketSinkHelper sink("ns3::UdpSocketFactory",
                              InetSocketAddress(Ipv4Address::GetAny(), fd.port));
        sink.SetAttribute("EnableSeqTsSizeHeader", BooleanValue(true));

        ApplicationContainer flowClients;
        ApplicationContainer flowServers;
        double earliestStart = 0.0;
        double latestStop = 0.0;

        for (uint32_t i = 0; i < nodes.GetN(); ++i)
        {
            // OnOff TX app: only on UEs in txNodes (or all if list is empty).
            if (allTx || txSet.count(i))
            {
                ApplicationContainer c = client.Install(nodes.Get(i));
                const double jitter = jitterRv->GetValue();
                c.Get(0)->SetStartTime(effectiveStart + Seconds(jitter));

                const double firstPktDelay =
                    static_cast<double>(pktSize) * 8.0 / DataRate(rateStr).GetBitRate();
                const double appStart = effectiveStart.GetSeconds() + jitter + firstPktDelay;
                const double appStop = appStart + simTime;
                c.Get(0)->SetStopTime(Seconds(appStop));

                earliestStart = std::max(earliestStart, appStart);
                latestStop = std::max(latestStop, appStop);
                flowClients.Add(c);
            }

            // PacketSink RX app: always on every UE so all nodes receive.
            ApplicationContainer s = sink.Install(nodes.Get(i));
            s.Get(0)->SetStartTime(Seconds(0.0));
            flowServers.Add(s);
        }

        result.defs.push_back(fd);
        result.clients.push_back(flowClients);
        result.servers.push_back(flowServers);
        result.appStart.push_back(earliestStart);
        result.appStop.push_back(latestStop);

        NS_LOG_INFO("Flow " << result.defs.size() << " (" << fd.label << "): port=" << fd.port
                            << " prio=" << +fd.priority
                            << " rri=" << fd.rri.GetMilliSeconds() << "ms"
                            << " pdb=" << fd.pdb.GetMilliSeconds() << "ms"
                            << " pktSize=" << pktSize << "B"
                            << " rate=" << rateStr
                            << (fd.dynamic ? " DG" : " SPS")
                            << " harq=" << (harqEnabled ? 1 : 0));
    }

    return result;
}

} // namespace ns3
