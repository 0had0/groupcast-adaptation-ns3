#pragma once
// nr_sl_stack.h
//
// Builds the complete NR + NR-Sidelink protocol stack on a NodeContainer:
//   1. Band / BWP creation (5.89 GHz, V2V_Highway, 20 MHz).
//   2. NrHelper attributes  (antenna 1×2 SISO, Tx=23 dBm, NrSlUeMac).
//   3. UE device installation + stream assignment.
//   4. NrSlHelper (NrEesmIrT1 error model, FixedMcs scheduler).
//   5. Resource pool + SidelinkPreconfigNr (TDD pattern, sensing off).
//   6. IP stack (IPv4) + default routes via EPC.
//
// Pattern follows sl-multi-lc-example.cc:372-555 and
// nr-v2x-west-to-east-highway.cc:660-929.

#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/nr-module.h"
#include "ns3/point-to-point-epc-helper.h"

#include <cstdint>

namespace ns3
{

// Parameters that vary across the PHY sweep (everything else is fixed
// to the 3GPP TR 37.885 V2V highway baseline).
struct SlStackParams
{
    uint32_t mu{1};      // numerology: 0 = 15 kHz SCS, 1 = 30 kHz, 2 = 60 kHz
    uint32_t bwPRBs{50}; // subchannel size W in PRBs (pool parameter)
    uint32_t mcs{14};    // fixed MCS index for NrSlUeMacSchedulerFixedMcs
    uint32_t seed{1};    // used for random-stream seeding
};

// Everything callers need after the stack is built.
struct SlStackCtx
{
    Ptr<NrPointToPointEpcHelper> epc;
    Ptr<NrHelper> nr;
    Ptr<NrSlHelper> sl;
    NetDeviceContainer ueNetDev;
    Ipv4InterfaceContainer ueIpIface;
    int64_t stream{0}; // next available random-stream index
};

// Installs the full NR-SL stack on `nodes` and returns the context.
// Caller must have already installed mobility models on `nodes`.
SlStackCtx BuildSlStack(const SlStackParams& p, const NodeContainer& nodes);

} // namespace ns3
