/*
 * compression_controller.cc
 *
 * See compression_controller.h for design notes.
 */

#include "compression_controller.h"

#include "ns3/double.h"
#include "ns3/log.h"
#include "ns3/simulator.h"
#include "ns3/uinteger.h"

#include <array>
#include <chrono>
#include <cmath>
#include <cstring>

#include <lz4.h>
#include <zstd.h>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("CompressionController");
NS_OBJECT_ENSURE_REGISTERED(CompressionController);

// ---------------------------------------------------------------------------
// TypeId / ctor / dtor
// ---------------------------------------------------------------------------

TypeId
CompressionController::GetTypeId()
{
    static TypeId tid = TypeId("ns3::CompressionController")
                            .SetParent<Object>()
                            .AddConstructor<CompressionController>();
    return tid;
}

CompressionController::CompressionController()
{
    m_synthRng = CreateObject<UniformRandomVariable>();
    m_synthRng->SetAttribute("Min", DoubleValue(0.0));
    m_synthRng->SetAttribute("Max", DoubleValue(1.0));
}

CompressionController::~CompressionController() = default;

// ---------------------------------------------------------------------------
// Setters / parsers
// ---------------------------------------------------------------------------

void CompressionController::SetMode(Mode m)            { m_mode = m; }
CompressionController::Mode CompressionController::GetMode() const { return m_mode; }

CompressionController::Mode
CompressionController::ParseMode(const std::string& s)
{
    if (s == "none")          return MODE_NONE;
    if (s == "fixed_lz4")     return MODE_FIXED_LZ4;
    if (s == "fixed_zstd1")   return MODE_FIXED_ZSTD1;
    if (s == "fixed_zstd3")   return MODE_FIXED_ZSTD3;
    if (s == "adaptive")      return MODE_ADAPTIVE;
    NS_FATAL_ERROR("Unknown compression mode: " << s);
    return MODE_NONE;
}

void CompressionController::SetTbSizeEstimator(std::function<uint32_t()> fn) { m_tbSizeFn = std::move(fn); }
void CompressionController::SetNackRateProvider(std::function<double()> fn)  { m_nackFn   = std::move(fn); }
void CompressionController::SetMaxDistanceProvider(std::function<double()> fn) { m_distFn = std::move(fn); }

void CompressionController::SetPdbMs(uint32_t pdb)                  { m_pdbMs = pdb; }
void CompressionController::SetTrafficClassPattern(const std::string& tc) { m_trafficClass = tc; }
void CompressionController::SetSeed(uint32_t seed)                  { m_seed = seed; }
void CompressionController::SetTxNodeIdAndFlow(uint32_t n, uint16_t f) { m_txNodeId = n; m_flowId = f; }
void CompressionController::SetTxLogCb(TxLogCb cb)                  { m_txLogCb = std::move(cb); }

void CompressionController::SetEntropyHighThr(double v)             { m_entropyHighThr  = v; }
void CompressionController::SetNackHighThr(double v)                { m_nackHighThr     = v; }
void CompressionController::SetDistFarThr(double v)                 { m_distFarThr      = v; }
void CompressionController::SetDeadlineSlackMs(double v)            { m_deadlineSlackMs = v; }

// ---------------------------------------------------------------------------
// Deterministic semi-compressible payload synth
//
// The goal is to produce bytes whose realistic compression ratios match the
// "structured V2X data" expectations from the plan:
//    periodic1 (~200 B)  : ~2x   (mostly low-entropy filler + small varying header)
//    periodic2 (~1 kB)   : ~3x   (table of struct records with bounded fields)
//    periodic3 (~45 kB)  : ~10-20x (LiDAR scan: int32 xyz with coords in [0,127]
//                                  → 75% zero bytes per record; ZSTD easily ≥10×)
//    aperiodic1 (~1 kB)  : ~2.5x (mixed CAM+event records)
//    aperiodic2 (~20 kB) : ~3x   (sparse perception snapshot: a few objects,
//                                  most slots empty)
// ---------------------------------------------------------------------------

namespace
{

// Stable hash for seeding from a string.
uint32_t HashString(const std::string& s)
{
    // FNV-1a 32-bit.
    uint32_t h = 0x811c9dc5u;
    for (char c : s)
    {
        h ^= static_cast<uint8_t>(c);
        h *= 0x01000193u;
    }
    return h;
}

// Deterministic LCG; cheap and reproducible.
class Lcg
{
  public:
    explicit Lcg(uint64_t seed) : m_s(seed | 1ull) {}
    uint32_t Next32()
    {
        m_s = m_s * 6364136223846793005ull + 1442695040888963407ull;
        return static_cast<uint32_t>(m_s >> 32);
    }
    double NextU01() { return static_cast<double>(Next32()) / static_cast<double>(UINT32_MAX); }
  private:
    uint64_t m_s;
};

void FillPeriodic1(std::vector<uint8_t>& v, Lcg& rng)
{
    // 12-byte header (seq32 + ts32 + status32) + zeros.
    const uint32_t n = v.size();
    if (n >= 12)
    {
        for (uint32_t i = 0; i < 12; ++i) v[i] = static_cast<uint8_t>(rng.Next32() & 0xff);
        std::memset(v.data() + 12, 0, n - 12);
    }
    else
    {
        for (uint32_t i = 0; i < n; ++i) v[i] = static_cast<uint8_t>(rng.Next32() & 0xff);
    }
}

void FillStructRecords(std::vector<uint8_t>& v, Lcg& rng, uint32_t recordSize, uint32_t lowBytes)
{
    // Each record = recordSize bytes, of which the last lowBytes are zero
    // (high-correlation) and the first (recordSize - lowBytes) are random.
    const uint32_t n = v.size();
    NS_ASSERT(lowBytes <= recordSize);
    const uint32_t hi = recordSize - lowBytes;
    for (uint32_t i = 0; i < n; i += recordSize)
    {
        const uint32_t end = std::min(i + recordSize, n);
        const uint32_t hiEnd = std::min(i + hi, end);
        for (uint32_t j = i; j < hiEnd; ++j) v[j] = static_cast<uint8_t>(rng.Next32() & 0xff);
        if (hiEnd < end) std::memset(v.data() + hiEnd, 0, end - hiEnd);
    }
}

void FillPointCloudLike(std::vector<uint8_t>& v, Lcg& rng)
{
    // Realistic LiDAR scan model: points stored as int32_t (x, y, z).
    // Coordinates are small integers in [0, 127] so bytes [1..3] of each
    // 4-byte field are always 0x00.  Eight cluster centres; each point adds
    // a ±3 delta to x/y only.  Result: ~75 % of payload bytes are 0x00,
    // giving ZSTD ratios of 0.05–0.10 (10–20×) — enough to collapse
    // N-segments from 19 to ≤ 2 within the 2.4 kB TB cap.
    const uint32_t n = v.size();
    static constexpr uint32_t REC = 12; // sizeof(int32_t) * 3
    const uint32_t nRec = n / REC;

    // Eight cluster centres, coordinates in [1, 8] (3 bits) so that byte[0]
    // of each int32 has low entropy and bytes[1..3] are always 0x00.
    // delta ∈ [-1, 1] → only 9 (dx,dy) combinations per cluster per z-layer
    // → 8 clusters × 9 positions × 2 z-layers = 144 distinct records.
    // With ~3 750 records drawn from 144 patterns, ZSTD matches nearly every
    // record, achieving ratio ≈ 0.05–0.08 (12–20×).
    static constexpr int32_t CENTRES[8][3] = {
        {2, 2, 0}, {2, 6, 0}, {6, 2, 0}, {6, 6, 0},
        {4, 4, 1}, {2, 4, 1}, {6, 4, 1}, {4, 2, 1},
    };

    std::memset(v.data(), 0, n);
    for (uint32_t r = 0; r < nRec; ++r)
    {
        const uint32_t ci = rng.Next32() & 7;
        // delta ∈ [-1, 1] on x and y; z is exact cluster value
        const int32_t dx = static_cast<int32_t>(rng.Next32() % 3) - 1;
        const int32_t dy = static_cast<int32_t>(rng.Next32() % 3) - 1;
        const int32_t xv = CENTRES[ci][0] + dx;
        const int32_t yv = CENTRES[ci][1] + dy;
        const int32_t zv = CENTRES[ci][2];
        uint8_t* p = v.data() + r * REC;
        std::memcpy(p,     &xv, 4);
        std::memcpy(p + 4, &yv, 4);
        std::memcpy(p + 8, &zv, 4);
        // On little-endian: byte[0]=value (0–127), bytes[1–3]=0x00.
        // zstd block-matching and run-length on the zero fields achieves
        // the target 10–20× ratio.
    }
    if (nRec * REC < n) std::memset(v.data() + nRec * REC, 0, n - nRec * REC);
}

void FillSparseObjects(std::vector<uint8_t>& v, Lcg& rng)
{
    // 20-byte object records. Only ~30% of slots populated; rest zero.
    const uint32_t n = v.size();
    static constexpr uint32_t REC = 20;
    const uint32_t nRec = n / REC;
    std::memset(v.data(), 0, n);
    for (uint32_t r = 0; r < nRec; ++r)
    {
        if (rng.NextU01() < 0.30)
        {
            // Populate first 12 bytes; remaining 8 stay zero.
            for (uint32_t j = 0; j < 12; ++j)
                v[r * REC + j] = static_cast<uint8_t>(rng.Next32() & 0xff);
        }
    }
}

} // anonymous namespace

std::vector<uint8_t>
CompressionController::SynthesisePayload(uint32_t origBytes)
{
    std::vector<uint8_t> v(origBytes);

    // Seed: stable per (run-seed, class, packet-sequence).
    const uint64_t s64 =
        (static_cast<uint64_t>(m_seed) << 32) ^ HashString(m_trafficClass) ^ m_pktSeq;
    Lcg rng(s64);

    if (m_trafficClass == "periodic1")
    {
        FillPeriodic1(v, rng);
    }
    else if (m_trafficClass == "periodic2")
    {
        FillStructRecords(v, rng, /*recordSize*/ 32, /*lowBytes*/ 20); // ~3x compressible
    }
    else if (m_trafficClass == "aperiodic1")
    {
        FillStructRecords(v, rng, /*recordSize*/ 24, /*lowBytes*/ 14); // ~2.5x compressible
    }
    else if (m_trafficClass == "periodic3")
    {
        FillPointCloudLike(v, rng);
    }
    else if (m_trafficClass == "aperiodic2")
    {
        FillSparseObjects(v, rng);
    }
    else
    {
        // Default: structured records.
        FillStructRecords(v, rng, /*recordSize*/ 32, /*lowBytes*/ 16);
    }
    return v;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

uint32_t
CompressionController::EstimateSegments(uint32_t bytes, uint32_t tbCap)
{
    if (tbCap == 0) return 1;
    return std::max(1u, (bytes + tbCap - 1) / tbCap);
}

double
CompressionController::EstimateEntropy(const std::vector<uint8_t>& data,
                                       uint32_t sampleBytes) const
{
    if (data.empty()) return 0.0;
    const uint32_t n = std::min(sampleBytes, static_cast<uint32_t>(data.size()));
    std::array<uint32_t, 256> cnt{};
    for (uint32_t i = 0; i < n; ++i) ++cnt[data[i]];
    double H = 0.0;
    for (uint32_t c : cnt)
    {
        if (c == 0) continue;
        const double p = static_cast<double>(c) / n;
        H -= p * std::log2(p);
    }
    return H / 8.0; // normalise to [0, 1] (max entropy for 8-bit symbols = 8)
}

CompressionTag::Codec
CompressionController::PickCodec(uint32_t origBytes,
                                 uint32_t tbCap,
                                 double   entropy,
                                 double   nackRate,
                                 double   maxDist,
                                 double   pdbSlackMs,
                                 std::string& reason) const
{
    switch (m_mode)
    {
        case MODE_NONE:        reason = "mode=none";         return CompressionTag::CODEC_NONE;
        case MODE_FIXED_LZ4:   reason = "mode=fixed_lz4";    return CompressionTag::CODEC_LZ4;
        case MODE_FIXED_ZSTD1: reason = "mode=fixed_zstd1";  return CompressionTag::CODEC_ZSTD_L1;
        case MODE_FIXED_ZSTD3: reason = "mode=fixed_zstd3";  return CompressionTag::CODEC_ZSTD_L3;
        case MODE_ADAPTIVE:    break;
    }

    const uint32_t N = EstimateSegments(origBytes, tbCap);
    if (N <= 1)
    {
        reason = "N=1 (fits one TB)";
        return CompressionTag::CODEC_NONE;
    }
    if (entropy > m_entropyHighThr)
    {
        reason = "high-entropy payload";
        return CompressionTag::CODEC_NONE;
    }
    if (pdbSlackMs < m_deadlineSlackMs)
    {
        reason = "deadline-pressure→LZ4";
        return CompressionTag::CODEC_LZ4;
    }
    if (nackRate > m_nackHighThr || maxDist > m_distFarThr)
    {
        reason = "channel-stress→ZSTD_L3";
        return CompressionTag::CODEC_ZSTD_L3;
    }
    reason = "default→ZSTD_L1";
    return CompressionTag::CODEC_ZSTD_L1;
}

std::pair<std::vector<uint8_t>, uint64_t>
CompressionController::RunCodec(Codec codec, const std::vector<uint8_t>& payload) const
{
    const auto t0 = std::chrono::steady_clock::now();
    std::vector<uint8_t> out;
    if (codec == CompressionTag::CODEC_NONE)
    {
        out = payload;
    }
    else if (codec == CompressionTag::CODEC_LZ4)
    {
        const int srcSz = static_cast<int>(payload.size());
        const int bound = LZ4_compressBound(srcSz);
        out.resize(bound);
        const int n = LZ4_compress_default(reinterpret_cast<const char*>(payload.data()),
                                           reinterpret_cast<char*>(out.data()),
                                           srcSz,
                                           bound);
        if (n <= 0)
        {
            NS_LOG_WARN("LZ4_compress_default failed; falling back to raw");
            out = payload;
        }
        else
        {
            out.resize(static_cast<size_t>(n));
        }
    }
    else // ZSTD_L1 or ZSTD_L3
    {
        const int level = (codec == CompressionTag::CODEC_ZSTD_L1) ? 1 : 3;
        const size_t bound = ZSTD_compressBound(payload.size());
        out.resize(bound);
        const size_t n = ZSTD_compress(out.data(), bound,
                                       payload.data(), payload.size(),
                                       level);
        if (ZSTD_isError(n))
        {
            NS_LOG_WARN("ZSTD_compress failed: " << ZSTD_getErrorName(n));
            out = payload;
        }
        else
        {
            out.resize(n);
        }
    }
    const auto t1 = std::chrono::steady_clock::now();
    const uint64_t us = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count());
    return {std::move(out), us};
}

// ---------------------------------------------------------------------------
// Decide(): the per-packet entry point
// ---------------------------------------------------------------------------

CompressionController::Decision
CompressionController::Decide(const std::vector<uint8_t>& payload)
{
    Decision dec;
    dec.tag.origSize = payload.size();

    const uint32_t tbCap = m_tbSizeFn ? m_tbSizeFn() : payload.size(); // conservative: assume fits
    const double nackR   = m_nackFn   ? m_nackFn()   : 0.0;
    const double maxD    = m_distFn   ? m_distFn()   : 0.0;

    // We don't track per-packet enqueue time here; use full PDB as slack.
    const double pdbSlackMs = static_cast<double>(m_pdbMs);

    dec.entropyEst = EstimateEntropy(payload);

    Codec codec = PickCodec(payload.size(),
                            tbCap,
                            dec.entropyEst,
                            nackR,
                            maxD,
                            pdbSlackMs,
                            dec.reason);

    auto [wire, encUs] = RunCodec(codec, payload);

    // If compressed result is not smaller, downgrade to NONE (avoid pessimisation).
    if (codec != CompressionTag::CODEC_NONE && wire.size() >= payload.size())
    {
        codec = CompressionTag::CODEC_NONE;
        wire  = payload;
        encUs = 0;
        dec.reason += "+expanded→NONE";
    }

    dec.tag.codec    = static_cast<uint8_t>(codec);
    dec.tag.compSize = wire.size();
    dec.tag.encodeUs = encUs;
    dec.tag.decodeUs = 0;
    m_lastTag        = dec.tag;

    // Build the on-wire byte stream:  [MAGIC(4) | codec(1) | origSize(4) |
    // encodeUs(8)] || compressed_bytes. Total prefix = 17 bytes. The magic
    // lets the RX hook distinguish compression-aware packets from legacy
    // (uncompressed) ones, since ns-3 PacketTags don't survive RLC
    // reassembly.
    constexpr uint32_t MAGIC = 0xCAFEC07Eu;
    const uint32_t origSz    = static_cast<uint32_t>(payload.size());
    const uint64_t encUs64   = encUs;
    std::vector<uint8_t> framed;
    framed.reserve(17 + wire.size());
    auto pushU32 = [&framed](uint32_t v) {
        framed.push_back(static_cast<uint8_t>(v       & 0xff));
        framed.push_back(static_cast<uint8_t>((v>>8 ) & 0xff));
        framed.push_back(static_cast<uint8_t>((v>>16) & 0xff));
        framed.push_back(static_cast<uint8_t>((v>>24) & 0xff));
    };
    auto pushU64 = [&framed](uint64_t v) {
        for (int i = 0; i < 8; ++i)
            framed.push_back(static_cast<uint8_t>((v >> (i*8)) & 0xff));
    };
    pushU32(MAGIC);
    framed.push_back(static_cast<uint8_t>(codec));
    pushU32(origSz);
    pushU64(encUs64);
    framed.insert(framed.end(), wire.begin(), wire.end());
    dec.wireBytes = std::move(framed);

    if (m_txLogCb)
    {
        const uint32_t N = EstimateSegments(dec.tag.compSize, tbCap);
        m_txLogCb(Simulator::Now().GetSeconds(),
                  m_txNodeId,
                  m_flowId,
                  m_pktSeq,
                  dec,
                  N,
                  tbCap);
    }
    ++m_pktSeq;
    return dec;
}

} // namespace ns3
