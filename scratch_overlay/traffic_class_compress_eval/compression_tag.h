/*
 * compression_tag.h
 *
 * Lightweight ns-3 PacketTag attached to every TX packet sent by the
 * CompressionController. Lets the RX-side KPI hook (a) detect that a packet
 * was compressed, (b) know the codec used and the original (pre-compression)
 * SDU size, (c) read the measured encode latency. The decode latency is
 * filled in by the RX hook (mutable in-place via RemovePacketTag/AddPacketTag).
 *
 * Serialised size: 25 bytes (1 + 4 + 4 + 8 + 8).
 */

#ifndef COMPRESSION_TAG_H
#define COMPRESSION_TAG_H

#include "ns3/tag.h"
#include "ns3/tag-buffer.h"
#include "ns3/type-id.h"

namespace ns3
{

class CompressionTag : public Tag
{
  public:
    enum Codec : uint8_t
    {
        CODEC_NONE     = 0,
        CODEC_LZ4      = 1,
        CODEC_ZSTD_L1  = 2,
        CODEC_ZSTD_L3  = 3
    };

    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::CompressionTag")
                                .SetParent<Tag>()
                                .AddConstructor<CompressionTag>();
        return tid;
    }

    TypeId GetInstanceTypeId() const override { return GetTypeId(); }

    uint32_t GetSerializedSize() const override
    {
        return 1u + 4u + 4u + 8u + 8u;
    }

    void Serialize(TagBuffer i) const override
    {
        i.WriteU8(codec);
        i.WriteU32(origSize);
        i.WriteU32(compSize);
        i.WriteU64(encodeUs);
        i.WriteU64(decodeUs);
    }

    void Deserialize(TagBuffer i) override
    {
        codec    = i.ReadU8();
        origSize = i.ReadU32();
        compSize = i.ReadU32();
        encodeUs = i.ReadU64();
        decodeUs = i.ReadU64();
    }

    void Print(std::ostream& os) const override
    {
        os << "CompressionTag{codec=" << static_cast<unsigned>(codec)
           << ", orig=" << origSize << ", comp=" << compSize
           << ", encUs=" << encodeUs << ", decUs=" << decodeUs << "}";
    }

    uint8_t  codec    = CODEC_NONE;
    uint32_t origSize = 0;
    uint32_t compSize = 0;
    uint64_t encodeUs = 0;
    uint64_t decodeUs = 0;
};

} // namespace ns3

#endif // COMPRESSION_TAG_H
