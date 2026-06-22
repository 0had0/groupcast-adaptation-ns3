#pragma once
// common/relay/same_l2id_relay_app.h
//
// SAME-L2ID relay for relay_storm_eval.
//
// Unlike FixedRelayApp (which uses a DIFFERENT L2 ID for the relay hop),
// SameL2IdRelayApp re-transmits every decoded leader packet on the SAME
// groupcast L2 ID, multicast group address, and UDP port as the leader.
//
// This is intentional: both hops contend in the same groupcast resource
// pool. Because all platoon nodes are members of the shared group, the
// relay's NACKs are heard by the leader (and vice-versa), triggering
// retransmissions from both sides — the "reTX storm" effect under study.
//
// The relay acts as a decode-and-forward repeater:
//   - Receives leader packets via the group's PacketSink RxWithSeqTsSize trace.
//   - Re-sends a copy (same seq, same size) on the same group:
//       dst = groupAddr (225.63.63.1), port = leaderPort, dstL2Id = 254.
//   - No PDB drop; every decoded packet is forwarded.
//   - Logs per-packet timing via an optional RelayTimingCb.
//
// Installation:
//   Call InstallSameL2IdRelay() once after flows are installed. It wires the
//   relay app to the relay node's leader-group PacketSink.

#include "common/flows/groupcast_flows.h"
#include "common/stack/nr_sl_stack.h"

#include "ns3/application.h"
#include "ns3/node-container.h"
#include "ns3/seq-ts-size-header.h"
#include "ns3/socket.h"

#include <functional>
#include <vector>

namespace ns3
{

// Per-packet relay timing log:
//   (seq, port, relayNodeId, leaderGenSec, relayDecodeSec, forwardSendSec, forwarded)
using RelayTimingCb =
    std::function<void(uint32_t, uint16_t, uint32_t, double, double, double, int)>;

class SameL2IdRelayApp : public Application
{
  public:
    static TypeId GetTypeId();
    SameL2IdRelayApp();
    ~SameL2IdRelayApp() override;

    // groupAddr/port must match the LEADER groupcast exactly.
    void Configure(Ipv4Address groupAddr, uint16_t port);

    void SetTimingCb(RelayTimingCb cb) { m_timingCb = std::move(cb); }

    // Bound to the relay node's leader-group PacketSink "RxWithSeqTsSize" trace.
    void OnLeaderRx(Ptr<const Packet> p,
                    const Address& src,
                    const Address& dst,
                    const SeqTsSizeHeader& hdr);

  private:
    void StartApplication() override;
    void StopApplication() override;

    Ipv4Address   m_groupAddr;
    uint16_t      m_port{0};
    RelayTimingCb m_timingCb;
    Ptr<Socket>   m_socket;
};

// Install a SameL2IdRelayApp on the relay node, wired to the first leader
// flow's PacketSink. The relay re-transmits on the same group/port/L2Id.
// Returns the installed SameL2IdRelayApp (caller may set timing callback).
//
// ueNodes       : full NodeContainer (9 nodes, node 0 = leader)
// ctx           : stack context (for RNTI etc.)
// flows         : installed flow result (to locate the relay's group-A sink)
// relayNodeId   : which node acts as relay (e.g. 4 or 6)
// flowIdx       : which flow the relay repeats (default 0 = first/leader flow)
// simTime       : sim duration (to stop the relay TX app)
Ptr<SameL2IdRelayApp> InstallSameL2IdRelay(const NodeContainer& ueNodes,
                                            const SlStackCtx& ctx,
                                            const FlowResult& flows,
                                            uint32_t relayNodeId,
                                            uint32_t flowIdx,
                                            double simTime);

} // namespace ns3
