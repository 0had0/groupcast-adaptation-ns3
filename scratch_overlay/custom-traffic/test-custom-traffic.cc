/*
 * Test script for CustomTrafficApplication
 *
 * This script tests all five traffic models:
 * - Periodic 1: 100 ms interval, packet sizes {300, 190, 190, 190, 190} bytes
 * - Periodic 2: 10 ms interval, 1200 bytes (0.2 prob) or 800 bytes (0.8 prob)
 * - Periodic 3: 30 ms interval, uniform 30,000-60,000 bytes, quantized to 10,000-byte steps
 * - Aperiodic 1: 50 ms + exp(50 ms) interval, uniform 200-2,000 bytes, quantized to 200-byte steps
 * - Aperiodic 2: 10 ms + exp(10 ms) interval, uniform 10,000-30,000 bytes, quantized to 4,000-byte steps
 */

#include "custom-traffic-application.h"
#include "custom-traffic-helper.h"

#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("CustomTrafficTest");

int
main(int argc, char* argv[])
{
    bool verbose = true;
    uint32_t testMode = 0; // 0=Periodic1, 1=Periodic2, 2=Periodic3, 3=Aperiodic1, 4=Aperiodic2
    double simTime = 2.0;   // Simulation time in seconds

    CommandLine cmd(__FILE__);
    cmd.AddValue("verbose", "Enable verbose logging", verbose);
    cmd.AddValue("mode", "Traffic mode to test (0-4)", testMode);
    cmd.AddValue("time", "Simulation time in seconds", simTime);
    cmd.Parse(argc, argv);

    if (verbose)
    {
        LogComponentEnable("CustomTrafficApplication", LOG_LEVEL_INFO);
        LogComponentEnable("CustomTrafficTest", LOG_LEVEL_INFO);
    }

    const char* modeNames[] = {"Periodic1", "Periodic2", "Periodic3", "Aperiodic1", "Aperiodic2"};
    NS_LOG_UNCOND("Testing CustomTrafficApplication with mode: " << modeNames[testMode]);

    // Create nodes
    NodeContainer nodes;
    nodes.Create(2);

    // Create point-to-point link
    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate", StringValue("100Mbps"));
    p2p.SetChannelAttribute("Delay", StringValue("1ms"));

    NetDeviceContainer devices = p2p.Install(nodes);

    // Install Internet stack
    InternetStackHelper internet;
    internet.Install(nodes);

    // Assign IP addresses
    Ipv4AddressHelper address;
    address.SetBase("10.1.1.0", "255.255.255.0");
    Ipv4InterfaceContainer interfaces = address.Assign(devices);

    // Create packet sink to receive packets
    uint16_t port = 9;
    PacketSinkHelper sinkHelper("ns3::UdpSocketFactory",
                                InetSocketAddress(Ipv4Address::GetAny(), port));
    ApplicationContainer sinkApps = sinkHelper.Install(nodes.Get(1));
    sinkApps.Start(Seconds(0.0));
    sinkApps.Stop(Seconds(simTime));

    // Create custom traffic application
    CustomTrafficHelper trafficHelper("ns3::UdpSocketFactory",
                                       InetSocketAddress(interfaces.GetAddress(1), port));
    trafficHelper.SetTrafficMode(testMode);

    ApplicationContainer trafficApps = trafficHelper.Install(nodes.Get(0));
    trafficApps.Start(Seconds(0.1));
    trafficApps.Stop(Seconds(simTime));

    // Enable tracing
    if (verbose)
    {
        p2p.EnableAsciiAll("custom-traffic-trace");
    }

    // Run simulation
    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    // Print statistics
    Ptr<PacketSink> sink = DynamicCast<PacketSink>(sinkApps.Get(0));
    NS_LOG_UNCOND("Total bytes received: " << sink->GetTotalRx());

    Simulator::Destroy();

    return 0;
}
