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

#include "custom-traffic-application.h"

#include "ns3/address.h"
#include "ns3/boolean.h"
#include "ns3/double.h"
#include "ns3/enum.h"
#include "ns3/log.h"
#include "ns3/node.h"
#include "ns3/nstime.h"
#include "ns3/packet.h"
#include "ns3/pointer.h"
#include "ns3/random-variable-stream.h"
#include "ns3/seq-ts-size-header.h"
#include "ns3/simulator.h"
#include "ns3/socket-factory.h"
#include "ns3/socket.h"
#include "ns3/string.h"
#include "ns3/trace-source-accessor.h"
#include "ns3/udp-socket-factory.h"
#include "ns3/uinteger.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("CustomTrafficApplication");

const uint32_t CustomTrafficApplication::PERIODIC1_SIZES[] = {300, 190, 190, 190, 190};
const uint32_t CustomTrafficApplication::PERIODIC1_SIZE_COUNT = 5;
const double CustomTrafficApplication::PERIODIC2_PROB_LARGE = 0.2;
const uint32_t CustomTrafficApplication::PERIODIC2_SIZE_LARGE = 1200;
const uint32_t CustomTrafficApplication::PERIODIC2_SIZE_SMALL = 800;
const uint32_t CustomTrafficApplication::PERIODIC3_MIN = 30000;
const uint32_t CustomTrafficApplication::PERIODIC3_MAX = 60000;
const uint32_t CustomTrafficApplication::PERIODIC3_STEP = 10000;
const double CustomTrafficApplication::APERIODIC1_BASE_INTERVAL = 50.0;
const double CustomTrafficApplication::APERIODIC1_EXP_MEAN = 50.0;
const uint32_t CustomTrafficApplication::APERIODIC1_MIN_SIZE = 200;
const uint32_t CustomTrafficApplication::APERIODIC1_MAX_SIZE = 2000;
const uint32_t CustomTrafficApplication::APERIODIC1_STEP = 200;
const double CustomTrafficApplication::APERIODIC2_BASE_INTERVAL = 10.0;
const double CustomTrafficApplication::APERIODIC2_EXP_MEAN = 10.0;
const uint32_t CustomTrafficApplication::APERIODIC2_MIN_SIZE = 10000;
const uint32_t CustomTrafficApplication::APERIODIC2_MAX_SIZE = 30000;
const uint32_t CustomTrafficApplication::APERIODIC2_STEP = 4000;

NS_OBJECT_ENSURE_REGISTERED(CustomTrafficApplication);

TypeId
CustomTrafficApplication::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::CustomTrafficApplication")
            .SetParent<Application>()
            .SetGroupName("Applications")
            .AddConstructor<CustomTrafficApplication>()
            .AddAttribute("TrafficMode",
                          "Traffic generation mode",
                          EnumValue(CustomTrafficApplication::PERIODIC_1),
                          MakeEnumAccessor<CustomTrafficApplication::TrafficMode>(&CustomTrafficApplication::m_trafficMode),
                          MakeEnumChecker(CustomTrafficApplication::PERIODIC_1,
                                          "Periodic1",
                                          CustomTrafficApplication::PERIODIC_2,
                                          "Periodic2",
                                          CustomTrafficApplication::PERIODIC_3,
                                          "Periodic3",
                                          CustomTrafficApplication::APERIODIC_1,
                                          "Aperiodic1",
                                          CustomTrafficApplication::APERIODIC_2,
                                          "Aperiodic2"))
            .AddAttribute("Remote",
                          "The address of the destination",
                          AddressValue(),
                          MakeAddressAccessor(&CustomTrafficApplication::m_peer),
                          MakeAddressChecker())
            .AddAttribute("Local",
                          "The Address on which to bind the socket. If not set, it is generated automatically.",
                          AddressValue(),
                          MakeAddressAccessor(&CustomTrafficApplication::m_local),
                          MakeAddressChecker())
            .AddAttribute("Tos",
                          "The Type of Service used to send IPv4 packets.",
                          UintegerValue(0),
                          MakeUintegerAccessor(&CustomTrafficApplication::m_tos),
                          MakeUintegerChecker<uint8_t>())
            .AddAttribute("EnableSeqTsSizeHeader",
                          "Prepend a SeqTsSizeHeader to every packet so the receiving "
                          "PacketSink (with the same flag set) can deserialize and "
                          "trace per-packet sequence numbers / timestamps / sizes.",
                          BooleanValue(true),
                          MakeBooleanAccessor(&CustomTrafficApplication::m_enableSeqTsSizeHeader),
                          MakeBooleanChecker())
            .AddAttribute("MaxBytes",
                          "The total number of bytes to send. Zero means no limit.",
                          UintegerValue(0),
                          MakeUintegerAccessor(&CustomTrafficApplication::m_maxBytes),
                          MakeUintegerChecker<uint64_t>())
            .AddAttribute("Protocol",
                          "The type of protocol to use.",
                          TypeIdValue(UdpSocketFactory::GetTypeId()),
                          MakeTypeIdAccessor(&CustomTrafficApplication::m_tid),
                          MakeTypeIdChecker())
            .AddAttribute("UniformRng",
                          "Uniform random variable stream for packet size selection.",
                          StringValue("ns3::UniformRandomVariable"),
                          MakePointerAccessor(&CustomTrafficApplication::m_uniformRng),
                          MakePointerChecker<RandomVariableStream>())
            .AddAttribute("ExponentialRng",
                          "Exponential random variable stream for interval variation.",
                          StringValue("ns3::ExponentialRandomVariable"),
                          MakePointerAccessor(&CustomTrafficApplication::m_expRng),
                          MakePointerChecker<RandomVariableStream>())
            .AddAttribute("BernoulliRng",
                          "Bernoulli random variable stream for probability-based selection.",
                          StringValue("ns3::BernoulliRandomVariable"),
                          MakePointerAccessor(&CustomTrafficApplication::m_bernoulliRng),
                          MakePointerChecker<RandomVariableStream>())
            .AddTraceSource("TxWithSeqTsSize",
                            "Fired after a packet is sent (only when EnableSeqTsSizeHeader=true). "
                            "Carries the packet, source/destination addresses, and the "
                            "SeqTsSizeHeader. Signature matches OnOffApplication so the "
                            "kpi_hooks bound callback can be reused.",
                            MakeTraceSourceAccessor(&CustomTrafficApplication::m_seqTsSizeTrace),
                            "ns3::PacketSink::SeqTsSizeCallback");
    return tid;
}

CustomTrafficApplication::CustomTrafficApplication()
    : m_socket(nullptr),
      m_connected(false),
      m_enableSeqTsSizeHeader(true),
      m_tos(0),
      m_trafficMode(CustomTrafficApplication::PERIODIC_1),
      m_periodic1Index(0),
      m_periodic1StartIndex(0),
      m_maxBytes(0),
      m_totBytes(0),
      m_seq(0)
{
    NS_LOG_FUNCTION(this);
}

CustomTrafficApplication::~CustomTrafficApplication()
{
    NS_LOG_FUNCTION(this);
}

void
CustomTrafficApplication::SetMaxBytes(uint64_t maxBytes)
{
    NS_LOG_FUNCTION(this << maxBytes);
    m_maxBytes = maxBytes;
}

Ptr<Socket>
CustomTrafficApplication::GetSocket() const
{
    NS_LOG_FUNCTION(this);
    return m_socket;
}

int64_t
CustomTrafficApplication::AssignStreams(int64_t stream)
{
    NS_LOG_FUNCTION(this << stream);
    m_uniformRng->SetStream(stream);
    m_expRng->SetStream(stream + 1);
    m_bernoulliRng->SetStream(stream + 2);
    return 3;
}

void
CustomTrafficApplication::SetSendBytesProvider(SendBytesProvider provider)
{
    m_sendBytesProvider = std::move(provider);
}

void
CustomTrafficApplication::SetTagDecorator(TagDecorator decorator)
{
    m_tagDecorator = std::move(decorator);
}

void
CustomTrafficApplication::DoDispose()
{
    NS_LOG_FUNCTION(this);
    m_socket = nullptr;
    Application::DoDispose();
}

void
CustomTrafficApplication::StartApplication()
{
    NS_LOG_FUNCTION(this);

    if (!m_socket)
    {
        m_socket = Socket::CreateSocket(GetNode(), m_tid);
        if (m_local.IsInvalid())
        {
            m_socket->Bind();
        }
        else
        {
            m_socket->Bind(m_local);
        }
        m_socket->Connect(m_peer);
        m_socket->SetAllowBroadcast(true);
        m_socket->ShutdownRecv();
    }

    m_socket->SetConnectCallback(MakeCallback(&CustomTrafficApplication::ConnectionSucceeded, this),
                                   MakeCallback(&CustomTrafficApplication::ConnectionFailed, this));

    m_periodic1StartIndex = DynamicCast<UniformRandomVariable>(m_uniformRng)->GetInteger(0, PERIODIC1_SIZE_COUNT - 1);
    m_periodic1Index = m_periodic1StartIndex;

    // Configure exponential RNG mean based on traffic mode
    Ptr<ExponentialRandomVariable> expRng = DynamicCast<ExponentialRandomVariable>(m_expRng);
    if (m_trafficMode == APERIODIC_1)
    {
        expRng->SetAttribute("Mean", DoubleValue(APERIODIC1_EXP_MEAN));
    }
    else if (m_trafficMode == APERIODIC_2)
    {
        expRng->SetAttribute("Mean", DoubleValue(APERIODIC2_EXP_MEAN));
    }

    NS_LOG_LOGIC("Periodic 1 starting index: " << m_periodic1StartIndex);

    ScheduleNextTx();
}

void
CustomTrafficApplication::StopApplication()
{
    NS_LOG_FUNCTION(this);
    CancelEvents();
    if (m_socket)
    {
        m_socket->Close();
    }
}

void
CustomTrafficApplication::CancelEvents()
{
    NS_LOG_FUNCTION(this);
    if (m_sendEvent.IsPending())
    {
        m_sendEvent.Cancel();
    }
}

void
CustomTrafficApplication::SendPacket(uint32_t packetSize)
{
    NS_LOG_FUNCTION(this << packetSize);

    NS_ASSERT(m_sendEvent.IsExpired());

    Ptr<Packet> packet;
    SeqTsSizeHeader header;
    if (m_enableSeqTsSizeHeader)
    {
        // Reserve room for the SeqTsSizeHeader so total wire size stays at
        // packetSize. Header carries (seq, timestamp, size).
        const uint32_t headerSize = SeqTsSizeHeader().GetSerializedSize();
        const uint32_t targetPayload = (packetSize > headerSize)
                                           ? (packetSize - headerSize)
                                           : 0;
        header.SetSeq(m_seq++);

        uint32_t wireBytes = targetPayload;
        if (m_sendBytesProvider)
        {
            std::vector<uint8_t> bytes = m_sendBytesProvider(targetPayload);
            if (!bytes.empty())
            {
                wireBytes = static_cast<uint32_t>(bytes.size());
                packet = Create<Packet>(bytes.data(), bytes.size());
            }
            else
            {
                packet = Create<Packet>(targetPayload);
            }
        }
        else
        {
            packet = Create<Packet>(targetPayload);
        }
        // header.SetSize() MUST equal the actual on-wire packet size
        // (payload + header), because PacketSink uses it as a stream-
        // reassembly threshold: bytes are buffered until GetSize() arrive,
        // then RxWithSeqTsSize fires. If we set it to the original SDU
        // size while sending only compressed bytes, PacketSink waits
        // forever and the trace never fires.
        header.SetSize(wireBytes + headerSize);
        if (m_tagDecorator)
        {
            m_tagDecorator(packet, targetPayload, wireBytes);
        }
        packet->AddHeader(header);
    }
    else
    {
        if (m_sendBytesProvider)
        {
            std::vector<uint8_t> bytes = m_sendBytesProvider(packetSize);
            if (!bytes.empty())
            {
                packet = Create<Packet>(bytes.data(), bytes.size());
            }
            else
            {
                packet = Create<Packet>(packetSize);
            }
        }
        else
        {
            packet = Create<Packet>(packetSize);
        }
        if (m_tagDecorator)
        {
            m_tagDecorator(packet, packetSize, packet->GetSize());
        }
    }

    Address from;
    m_socket->GetSockName(from);
    int actual = m_socket->Send(packet);
    if (actual > 0)
    {
        m_totBytes += static_cast<uint32_t>(actual);
        if (m_enableSeqTsSizeHeader)
        {
            m_seqTsSizeTrace(packet, from, m_peer, header);
        }
        NS_LOG_INFO("At time " << Simulator::Now().As(Time::S) << " sent " << actual
                               << " bytes (target " << packetSize << "), total Tx "
                               << m_totBytes << " bytes");
    }
    else
    {
        NS_LOG_ERROR("Unable to send packet; actual " << actual << " size " << packetSize);
    }

    ScheduleNextTx();
}

void
CustomTrafficApplication::ScheduleNextTx()
{
    NS_LOG_FUNCTION(this);

    if (m_maxBytes == 0 || m_totBytes < m_maxBytes)
    {
        Time nextInterval;
        uint32_t packetSize;

        switch (m_trafficMode)
        {
            case CustomTrafficApplication::PERIODIC_1:
                nextInterval = MilliSeconds(100);
                packetSize = PERIODIC1_SIZES[m_periodic1Index];
                m_periodic1Index = (m_periodic1Index + 1) % PERIODIC1_SIZE_COUNT;
                break;

            case CustomTrafficApplication::PERIODIC_2:
                nextInterval = MilliSeconds(10);
                if (DynamicCast<BernoulliRandomVariable>(m_bernoulliRng)->GetValue(PERIODIC2_PROB_LARGE) == 1.0)
                {
                    packetSize = PERIODIC2_SIZE_LARGE;
                }
                else
                {
                    packetSize = PERIODIC2_SIZE_SMALL;
                }
                break;

            case CustomTrafficApplication::PERIODIC_3:
                nextInterval = MilliSeconds(30);
                packetSize = QuantizeUniform(PERIODIC3_MIN, PERIODIC3_MAX, PERIODIC3_STEP);
                break;

            case CustomTrafficApplication::APERIODIC_1:
                {
                    // Mean already set in StartApplication()
                    Ptr<ExponentialRandomVariable> expRng = DynamicCast<ExponentialRandomVariable>(m_expRng);
                    nextInterval = MilliSeconds(APERIODIC1_BASE_INTERVAL) + MilliSeconds(static_cast<uint32_t>(expRng->GetValue()));
                    packetSize = QuantizeUniform(APERIODIC1_MIN_SIZE, APERIODIC1_MAX_SIZE, APERIODIC1_STEP);
                }
                break;

            case CustomTrafficApplication::APERIODIC_2:
                {
                    // Mean already set in StartApplication()
                    Ptr<ExponentialRandomVariable> expRng = DynamicCast<ExponentialRandomVariable>(m_expRng);
                    nextInterval = MilliSeconds(APERIODIC2_BASE_INTERVAL) + MilliSeconds(static_cast<uint32_t>(expRng->GetValue()));
                    packetSize = QuantizeUniform(APERIODIC2_MIN_SIZE, APERIODIC2_MAX_SIZE, APERIODIC2_STEP);
                }
                break;

            default:
                NS_FATAL_ERROR("Invalid traffic mode");
                return;
        }

        m_sendEvent = Simulator::Schedule(nextInterval, &CustomTrafficApplication::SendPacket, this, packetSize);
    }
    else
    {
        StopApplication();
    }
}

uint32_t
CustomTrafficApplication::QuantizeUniform(uint32_t min, uint32_t max, uint32_t step)
{
    NS_LOG_FUNCTION(this << min << max << step);

    uint32_t numSteps = (max - min) / step;
    uint32_t stepIndex = DynamicCast<UniformRandomVariable>(m_uniformRng)->GetInteger(0, numSteps);
    uint32_t quantizedValue = min + (stepIndex * step);

    NS_LOG_LOGIC("Quantized value: " << quantizedValue << " (min=" << min << ", max=" << max
                                      << ", step=" << step << ", stepIndex=" << stepIndex << ")");

    return quantizedValue;
}

void
CustomTrafficApplication::ConnectionSucceeded(Ptr<Socket> socket)
{
    NS_LOG_FUNCTION(this << socket);
    m_connected = true;
}

void
CustomTrafficApplication::ConnectionFailed(Ptr<Socket> socket)
{
    NS_LOG_FUNCTION(this << socket);
    m_connected = false;
}

} // namespace ns3
