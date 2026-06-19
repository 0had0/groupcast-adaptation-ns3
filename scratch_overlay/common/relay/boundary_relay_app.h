#pragma once
// relay_app.h
//
// L2 decode-and-forward RELAY for groupcast, implemented entirely in scratch
// (no ns-3-dev edits). A BoundaryRelayApp lives on every follower UE. On each LEADER
// packet that this UE decodes (hooked via the leader PacketSink's
// RxWithSeqTsSize trace), the app:
//   1. STRICT-CLOSEST election: among all candidate followers it reads live
//      positions and computes |dist_to_TX - boundary|; it proceeds only if it
//      is the unique closest node to the boundary. If the closest node did not
//      decode this packet, its callback never fires → no relay copy ("skip").
//   2. PDB gate: drops the packet if it is already older than the original PDB.
//   3. Re-broadcasts a copy (same app sequence number) on a SECOND groupcast
//      link (distinct dstL2Id/port, Broadcast cast type → no PSFCH, no NACK
//      storm). Far UEs that also subscribe to this link get a second chance to
//      decode (cooperative union reception).
//
// InstallRelayGroup() wires one such relay link: activates the Broadcast
// bearer, installs RX sinks (appended to the FlowResult so kpi_hooks logs them),
// and installs + connects the RelayApps.

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

// Relay-selection log sink: (timeSec, boundaryM, electedNodeId, distToTxM,
// seq, forwarded[1=sent / 0=dropped-late]).
using RelaySelectCb =
    std::function<void(double, double, uint32_t, double, uint32_t, int)>;

class BoundaryRelayApp : public Application
{
  public:
    static TypeId GetTypeId();
    BoundaryRelayApp();
    ~BoundaryRelayApp() override;

    // candidates : all UE nodes (the TX node is skipped during election).
    void Configure(NodeContainer candidates,
                   uint32_t txNodeId,
                   Ipv4Address groupAddr,
                   uint16_t relayPort,
                   double boundaryM,
                   Time pdb);

    void SetLogCb(RelaySelectCb cb) { m_logCb = std::move(cb); }

    // Bound to the leader PacketSink "RxWithSeqTsSize" trace on this node.
    void OnLeaderRx(Ptr<const Packet> p,
                    const Address& src,
                    const Address& dst,
                    const SeqTsSizeHeader& hdr);

  private:
    void StartApplication() override;
    void StopApplication() override;

    double DistToTx(Ptr<Node> n) const;
    bool AmIElected() const; // strict-closest to boundary among candidates

    NodeContainer m_candidates;
    Ptr<Node> m_txNode;
    Ipv4Address m_groupAddr;
    uint16_t m_relayPort{0};
    double m_boundaryM{0.0};
    Time m_pdb;
    RelaySelectCb m_logCb;
    Ptr<Socket> m_socket;
};

// Activate one Broadcast relay groupcast link at `boundaryM`. Appends the relay
// RX sinks to `flows` (so ConnectAppTraces logs them under `relayPort`) and
// returns the BoundaryRelayApp instances (log callbacks set later by the caller).
std::vector<Ptr<BoundaryRelayApp>> InstallRelayGroup(const NodeContainer& ueNodes,
                                             const SlStackCtx& ctx,
                                             FlowResult& flows,
                                             uint32_t txNodeId,
                                             const ApplicationContainer& leaderSinks,
                                             uint16_t leaderPort,
                                             uint16_t relayPort,
                                             uint32_t relayDstL2Id,
                                             double boundaryM,
                                             Time pdb,
                                             double simTime);

} // namespace ns3
