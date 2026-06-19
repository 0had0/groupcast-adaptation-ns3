# Custom Traffic Application for ns-3

A custom Application class and Helper class for ns-3 that simulates five specific data traffic models.

## Traffic Models

| Model | Interval | Packet Size |
|-------|----------|-------------|
| Periodic 1 | 100 ms | {300, 190, 190, 190, 190} bytes (random start index) |
| Periodic 2 | 10 ms | 1200 bytes (0.2 prob) or 800 bytes (0.8 prob) |
| Periodic 3 | 30 ms | Uniform 30,000-60,000 bytes, quantized to 10,000-byte steps |
| Aperiodic 1 | 50 ms + exp(50 ms) | Uniform 200-2,000 bytes, quantized to 200-byte steps |
| Aperiodic 2 | 10 ms + exp(10 ms) | Uniform 10,000-30,000 bytes, quantized to 4,000-byte steps |

## Files

- `custom-traffic-application.h` - Application class header
- `custom-traffic-application.cc` - Application class implementation
- `custom-traffic-helper.h` - Helper class header
- `custom-traffic-helper.cc` - Helper class implementation
- `test-custom-traffic.cc` - Test/example script

## Usage Example

```cpp
#include "custom-traffic-helper.h"

// Create helper
CustomTrafficHelper helper("ns3::UdpSocketFactory",
                           InetSocketAddress(Ipv4Address("10.0.0.2"), 8080));

// Set traffic mode (0=Periodic1, 1=Periodic2, 2=Periodic3, 3=Aperiodic1, 4=Aperiodic2)
helper.SetTrafficMode(3); // Aperiodic 1

// Install on node
ApplicationContainer apps = helper.Install(node);

// Start and stop
apps.Start(Seconds(1.0));
apps.Stop(Seconds(10.0));

// Assign RNG streams for reproducibility
helper.AssignStreams(NodeContainer(node), 100);
```

## Building

To build the test script:

```bash
cd /path/to/ns-3-dev
./ns3 build scratch_overlay/custom-traffic/test-custom-traffic
```

## Running

To run the test script:

```bash
./ns3 run "scratch_overlay/custom-traffic/test-custom-traffic --mode=0 --time=2.0"
```

Options:
- `--mode=0-4`: Select traffic mode (default: 0)
- `--time=X.X`: Simulation time in seconds (default: 2.0)
- `--verbose`: Enable verbose logging

## Features

- **Mode Selection**: Enum attribute to select between 5 traffic models
- **Reproducibility**: Uses ns3::RandomVariableStream for all stochastic properties
- **Accurate Scheduling**: Implements ScheduleNextTx() for precise inter-packet arrival times
- **Quantization**: Helper method for quantized packet size selection
- **Random Start Index**: Periodic 1 uses random starting index for each instance
