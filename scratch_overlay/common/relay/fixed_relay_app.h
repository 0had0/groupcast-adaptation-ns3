#pragma once
// relay_app.h  (static_relay_collision_eval)
//
// FIXED L2 decode-and-forward RELAY for a static platoon, implemented entirely
// in scratch (no ns-3-dev edits). The relay is a single, fixed node i that:
//   1. is a member of the LEADER groupcast (Group A, dstL2Id 254) and decodes
//      the leader's packets via that group's PacketSink RxWithSeqTsSize trace;
//   2. on every decoded leader packet, immediately re-transmits a copy carrying
//      the SAME app sequence number on the RELAY groupcast (Group B, dstL2Id
//      255) which serves only the tail nodes i+1..N-1 (strict partition);
//   3. does NOT drop on PDB — it forwards every decoded packet and LOGS the
//      per-packet timing so the analysis can compute the relay's dynamic
//      remaining budget and the end-to-end PDB violations.
//
// Group B is a GROUPCAST with its own PSFCH ACK/NACK feedback (same SB-SPS
// config as the leader) so the relay hop has real HARQ; the only intended
// difference from Group A is the (dynamic, per-packet) delay budget.
//
// InstallFixedRelay() activates the Group-B bearer on {relay ∪ tail}, installs
// RX sinks on the tail nodes (appended to FlowResult so kpi_hooks logs them),
// and installs one FixedRelayApp on the relay node wired to its Group-A sink.

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

// Per-packet relay timing log sink:
//   (seq, origPort, relayNodeId, leaderGenSec, relayDecodeSec, forwardSendSec, forwarded)
// leaderGenSec   = leader's packet generation time (SeqTsSizeHeader ts of Group A)
// relayDecodeSec = time the relay's app received (decoded) the leader packet
// forwardSendSec = time the relay handed the forwarded copy to the socket
// forwarded      = 1 (sent) ; 0 reserved (relay never drops here)
using RelayTimingCb =
    std::function<void(uint32_t, uint16_t, uint32_t, double, double, double, int)>;

class FixedRelayApp : public Application
{
  public:
    static TypeId GetTypeId();
    FixedRelayApp();
    ~FixedRelayApp() override;

    // groupAddr/relayPort identify the Group-B socket; leaderPort tags the log.
    void Configure(Ipv4Address groupAddr, uint16_t relayPort, uint16_t leaderPort);

    void SetTimingCb(RelayTimingCb cb) { m_timingCb = std::move(cb); }

    // Bound to the relay node's Group-A PacketSink "RxWithSeqTsSize" trace.
    void OnLeaderRx(Ptr<const Packet> p,
                    const Address& src,
                    const Address& dst,
                    const SeqTsSizeHeader& hdr);

  private:
    void StartApplication() override;
    void StopApplication() override;

    Ipv4Address m_groupAddr;
    uint16_t m_relayPort{0};
    uint16_t m_leaderPort{0};
    RelayTimingCb m_timingCb;
    Ptr<Socket> m_socket;
};

// Activate one Group-B relay groupcast link served by the fixed relay node.
// tailNodes = the RX set i+1..N-1 (strict partition); relayLeaderSink = the
// Group-A PacketSink installed on the relay node. rri/dynamic mirror the leader
// flow so both hops share the same SB-SPS configuration. Returns the FixedRelayApp
// (timing log callback set by the caller).
Ptr<FixedRelayApp> InstallFixedRelay(const NodeContainer& ueNodes,
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
                                double simTime);

} // namespace ns3
