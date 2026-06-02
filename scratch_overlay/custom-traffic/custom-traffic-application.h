/*
 * Copyright (c) 2026
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation;
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 */

#ifndef CUSTOM_TRAFFIC_APPLICATION_H
#define CUSTOM_TRAFFIC_APPLICATION_H

#include "ns3/address.h"
#include "ns3/application.h"
#include "ns3/event-id.h"
#include "ns3/ptr.h"
#include "ns3/seq-ts-size-header.h"
#include "ns3/traced-callback.h"

#include <cstdint>
#include <functional>
#include <vector>

namespace ns3
{

class RandomVariableStream;
class Socket;

class CustomTrafficApplication : public Application
{
  public:
    enum TrafficMode
    {
        PERIODIC_1,
        PERIODIC_2,
        PERIODIC_3,
        APERIODIC_1,
        APERIODIC_2
    };

    /**
     * Optional pre-send byte provider. When set, SendPacket() asks the
     * provider to produce the actual on-wire bytes for the requested target
     * payload size. The provider may return a *smaller* (compressed) buffer
     * — the wire-side packet size will be set to the returned buffer size,
     * but the SeqTsSizeHeader still records the original `targetBytes` so
     * downstream KPI accounting (PIR, throughput) sees application-level
     * SDU size. A second callback (TagDecorator) is given the freshly
     * constructed Packet so the caller can AddPacketTag() (e.g. attach a
     * CompressionTag describing codec + sizes).
     *
     * If no provider is set, behaviour is byte-identical to the legacy
     * zero-fill path.
     */
    using SendBytesProvider =
        std::function<std::vector<uint8_t>(uint32_t /*targetBytes*/)>;
    using TagDecorator = std::function<void(Ptr<Packet> /*pkt*/,
                                            uint32_t   /*targetBytes*/,
                                            uint32_t   /*wireBytes*/)>;

    static TypeId GetTypeId();

    CustomTrafficApplication();

    ~CustomTrafficApplication() override;

    void SetMaxBytes(uint64_t maxBytes);

    Ptr<Socket> GetSocket() const;

    int64_t AssignStreams(int64_t stream) override;

    /// Install a pre-send byte provider (optional). Pass nullptr to disable.
    void SetSendBytesProvider(SendBytesProvider provider);
    void SetTagDecorator(TagDecorator decorator);

  protected:
    void DoDispose() override;

  private:
    void StartApplication() override;
    void StopApplication() override;

    void CancelEvents();
    void SendPacket(uint32_t packetSize);
    void ScheduleNextTx();
    uint32_t QuantizeUniform(uint32_t min, uint32_t max, uint32_t step);

    void ConnectionSucceeded(Ptr<Socket> socket);
    void ConnectionFailed(Ptr<Socket> socket);

    Ptr<Socket> m_socket;
    Address m_peer;
    Address m_local;
    bool m_connected;
    bool m_enableSeqTsSizeHeader;
    uint8_t m_tos;
    Ptr<RandomVariableStream> m_uniformRng;
    Ptr<RandomVariableStream> m_expRng;
    Ptr<RandomVariableStream> m_bernoulliRng;
    TrafficMode m_trafficMode;
    uint32_t m_periodic1Index;
    uint32_t m_periodic1StartIndex;
    uint64_t m_maxBytes;
    uint64_t m_totBytes;
    EventId m_sendEvent;
    TypeId m_tid;
    uint32_t m_seq;

    // Trace source fired when EnableSeqTsSizeHeader=true. Signature matches
    // OnOffApplication::TxWithSeqTsSize so kpi_hooks can reuse the same
    // bound callback for both application types.
    TracedCallback<Ptr<const Packet>,
                   const Address&,
                   const Address&,
                   const SeqTsSizeHeader&>
        m_seqTsSizeTrace;

    static const uint32_t PERIODIC1_SIZES[];
    static const uint32_t PERIODIC1_SIZE_COUNT;
    static const double PERIODIC2_PROB_LARGE;
    static const uint32_t PERIODIC2_SIZE_LARGE;
    static const uint32_t PERIODIC2_SIZE_SMALL;
    static const uint32_t PERIODIC3_MIN;
    static const uint32_t PERIODIC3_MAX;
    static const uint32_t PERIODIC3_STEP;
    static const double APERIODIC1_BASE_INTERVAL;
    static const double APERIODIC1_EXP_MEAN;
    static const uint32_t APERIODIC1_MIN_SIZE;
    static const uint32_t APERIODIC1_MAX_SIZE;
    static const uint32_t APERIODIC1_STEP;
    static const double APERIODIC2_BASE_INTERVAL;
    static const double APERIODIC2_EXP_MEAN;
    static const uint32_t APERIODIC2_MIN_SIZE;
    static const uint32_t APERIODIC2_MAX_SIZE;
    static const uint32_t APERIODIC2_STEP;

    SendBytesProvider m_sendBytesProvider;
    TagDecorator m_tagDecorator;
};

} // namespace ns3

#endif /* CUSTOM_TRAFFIC_APPLICATION_H */
