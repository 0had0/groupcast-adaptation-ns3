/*
 * compression_controller.h
 *
 * Per-flow adaptive compression decision-maker for NR-SL application-layer
 * payloads. One instance per CustomTrafficApplication. The controller:
 *
 *   1. Synthesises deterministic, semi-compressible bytes for a given traffic
 *      class (so real zstd/lz4 hit realistic ratios — replaces the zero-fill
 *      that ns-3 would otherwise produce).
 *   2. Picks a codec per packet based on an adaptive decision tree using:
 *        - estimated number of RLC segments (N = ceil(orig / TbCap))
 *        - byte-level entropy estimate (skip codec if data is incompressible)
 *        - PSFCH NACK rate over a recent window
 *        - max RX distance from the mobility model
 *        - remaining PDB slack vs deadline
 *   3. Compresses the synthesised bytes with the selected codec (zstd/lz4).
 *   4. Emits a CompressionLog row via a callback.
 *
 * The TX integration point is CustomTrafficApplication::SetSendBytesProvider
 * (added in the small backward-compatible patch). When the provider returns
 * a non-empty byte vector, the application sends that buffer instead of the
 * default zero-filled payload; a CompressionTag is also attached.
 */

#ifndef COMPRESSION_CONTROLLER_H
#define COMPRESSION_CONTROLLER_H

#include "compression_tag.h"

#include "ns3/object.h"
#include "ns3/random-variable-stream.h"

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace ns3
{

class CompressionController : public Object
{
  public:
    using Codec = CompressionTag::Codec;

    enum Mode
    {
        MODE_NONE         = 0, // never compress, but still attach tag (origSize=compSize)
        MODE_FIXED_LZ4    = 1,
        MODE_FIXED_ZSTD1  = 2,
        MODE_FIXED_ZSTD3  = 3,
        MODE_ADAPTIVE     = 4
    };

    struct Decision
    {
        std::vector<uint8_t> wireBytes;  // bytes to put on the wire (post-codec)
        CompressionTag       tag;        // codec, sizes, encodeUs already filled
        double               entropyEst = 0.0;
        std::string          reason;     // human-readable, persisted to compressionLog
    };

    /// Callback fired once per TX decision. Args: (timeSec, txNodeId, flowId,
    /// pktSeq, decision-record, nSegments, tbSize).
    using TxLogCb = std::function<void(double,
                                       uint32_t,
                                       uint16_t,
                                       uint32_t,
                                       const Decision&,
                                       uint32_t,
                                       uint32_t)>;

    static TypeId GetTypeId();

    CompressionController();
    ~CompressionController() override;

    void SetMode(Mode m);
    Mode GetMode() const;
    static Mode ParseMode(const std::string& s);

    /// External-state providers consulted on each TX decision.
    void SetTbSizeEstimator(std::function<uint32_t()> fn);
    void SetNackRateProvider(std::function<double()> fn);
    void SetMaxDistanceProvider(std::function<double()> fn);

    void SetPdbMs(uint32_t pdb);
    void SetTrafficClassPattern(const std::string& tc);
    void SetSeed(uint32_t seed);
    void SetTxNodeIdAndFlow(uint32_t nodeId, uint16_t flowId);
    void SetTxLogCb(TxLogCb cb);

    /// Adaptive thresholds (CLI-tunable).
    void SetEntropyHighThr(double v);
    void SetNackHighThr(double v);
    void SetDistFarThr(double v);
    void SetDeadlineSlackMs(double v);

    /// Generate semi-compressible bytes of the requested SDU size.
    std::vector<uint8_t> SynthesisePayload(uint32_t origBytes);

    /// Compress the supplied payload according to mode + signals. Returns the
    /// wire-ready bytes plus a fully-populated CompressionTag. Also fires the
    /// TxLogCb when set. The decision is cached as m_lastDecision for the
    /// downstream TagDecorator to attach the tag without re-running the codec.
    Decision Decide(const std::vector<uint8_t>& payload);

    /// Read back the tag attached to the most-recent Decide() call.
    const CompressionTag& GetLastTag() const { return m_lastTag; }

    /// Helper: number of RLC segments needed to carry an SDU of given size
    /// across TB caps. ceil(size / TbCap). Returns at least 1.
    static uint32_t EstimateSegments(uint32_t bytes, uint32_t tbCap);

  private:
    Mode m_mode = MODE_ADAPTIVE;

    std::function<uint32_t()> m_tbSizeFn;
    std::function<double()>   m_nackFn;
    std::function<double()>   m_distFn;

    uint32_t m_pdbMs                = 100;
    double   m_entropyHighThr       = 0.92;
    double   m_nackHighThr          = 0.10;
    double   m_distFarThr           = 80.0;
    double   m_deadlineSlackMs      = 2.0;

    std::string m_trafficClass      = "periodic1";
    uint32_t    m_seed              = 1;
    uint32_t    m_txNodeId          = 0;
    uint16_t    m_flowId            = 0;
    uint32_t    m_pktSeq            = 0;
    TxLogCb     m_txLogCb;

    // Deterministic RNG seeded from m_seed + traffic class hash.
    Ptr<UniformRandomVariable> m_synthRng;

    // Cached most-recent decision (read by TagDecorator).
    CompressionTag m_lastTag;

    /// Shannon byte-level entropy of a sample, normalised to [0, 1].
    /// Uses only the first `sampleBytes` bytes for speed.
    double EstimateEntropy(const std::vector<uint8_t>& data, uint32_t sampleBytes = 512) const;

    /// Pick codec via adaptive tree.
    Codec PickCodec(uint32_t origBytes,
                    uint32_t tbCap,
                    double   entropy,
                    double   nackRate,
                    double   maxDist,
                    double   pdbSlackMs,
                    std::string& reason) const;

    /// Actually compress; returns wire bytes + measured encodeUs.
    std::pair<std::vector<uint8_t>, uint64_t> RunCodec(
        Codec codec, const std::vector<uint8_t>& payload) const;
};

} // namespace ns3

#endif // COMPRESSION_CONTROLLER_H
