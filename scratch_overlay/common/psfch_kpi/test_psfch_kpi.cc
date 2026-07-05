// common/psfch_kpi/test_psfch_kpi.cc
//
// Smoke test for PsfchKpiCollector. Drives the collector directly with
// synthesized events and asserts:
//   - ACK and NACK arrivals are recorded.
//   - SILENCE is emitted for missing RXs when an opportunity closes.
//   - CastType filtering: Broadcast dstL2Ids never produce events.
//   - End-of-sim cleanup emits SILENCE for still-open opportunities.
//
// The collector is a singleton; the test calls ResetForTest() between cases.

#include "common/mcd/mcr_feedback_gate.h"
#include "common/psfch_kpi/psfch_kpi.h"
#include "ns3/nr-phy-mac-common.h"

#include <cassert>
#include <cstdio>
#include <set>
#include <string>

using namespace ns3;

// Helper: count events of a given status for a (sender, dst, pid) tuple.
static uint32_t
CountStatus(const std::vector<PsFchEvent>& evs,
            uint32_t sender, uint32_t dst, uint8_t pid, uint8_t status)
{
    uint32_t n = 0;
    for (const auto& e : evs)
    {
        if (e.senderNodeId == sender && e.dstL2Id == dst && e.harqPid == pid &&
            e.status == status)
        {
            ++n;
        }
    }
    return n;
}

static SlHarqInfo
MakeHarqInfo(uint32_t txRnti, uint32_t dstL2Id, uint8_t pid, bool ack)
{
    SlHarqInfo info;
    info.m_txRnti = static_cast<uint16_t>(txRnti);
    info.m_dstL2Id = static_cast<uint16_t>(dstL2Id);
    info.m_harqProcessId = pid;
    info.m_bwpIndex = 0;
    info.m_harqStatus = ack ? SlHarqInfo::ACK : SlHarqInfo::NACK;
    return info;
}

#define ASSERT_TRUE(cond)                                                           \
    do                                                                              \
    {                                                                               \
        if (!(cond))                                                                \
        {                                                                           \
            std::fprintf(stderr, "ASSERT FAIL [%s:%d] %s\n", __FILE__, __LINE__, #cond); \
            return 1;                                                               \
        }                                                                           \
    } while (0)

int
main()
{
    // ---------------------------------------------------------------------
    // Test 1: basic ACK/NACK recording.
    // ---------------------------------------------------------------------
    {
        auto& col = PsfchKpiCollector::Instance();
        col.ResetForTest();
        std::set<uint32_t> members{0, 1, 2, 3};
        col.RegisterDstL2IdMembers(254, members);

        // Sender 0 sends TB; RX 1, 2, 3 respond with ACK/NACK.
        SlHarqInfo info;
        info = MakeHarqInfo(/*txRnti=*/0, /*dstL2Id=*/254, /*pid=*/0, /*ack=*/true);
        col.RecordArrival(/*sender=*/0, /*rx=*/1, info, 0.100, /*sfn=*/100000);
        info = MakeHarqInfo(0, 254, 0, /*ack=*/false);
        col.RecordArrival(0, /*rx=*/2, info, 0.100, 100000);
        info = MakeHarqInfo(0, 254, 0, /*ack=*/true);
        col.RecordArrival(0, /*rx=*/3, info, 0.100, 100000);

        ASSERT_TRUE(CountStatus(col.Events(), 0, 254, 0, /*STATUS_ACK=*/0) == 2);
        ASSERT_TRUE(CountStatus(col.Events(), 0, 254, 0, /*STATUS_NACK=*/1) == 1);
    }

    // ---------------------------------------------------------------------
    // Test 2: SILENCE emitted when new opportunity begins and previous
    //         one had missing RXs.
    // ---------------------------------------------------------------------
    {
        auto& col = PsfchKpiCollector::Instance();
        col.ResetForTest();
        std::set<uint32_t> members{0, 1, 2, 3};
        col.RegisterDstL2IdMembers(254, members);

        // Opp 1 (sfn=100): only RX 1 responds.
        SlHarqInfo info = MakeHarqInfo(0, 254, /*pid=*/0, /*ack=*/true);
        col.RecordArrival(0, 1, info, 0.100, 100000);

        // Opp 2 (sfn=200): only RX 3 responds. Opp 1 should close and
        // emit SILENCE for RX 2 and RX 3.
        info = MakeHarqInfo(0, 254, 0, /*ack=*/true);
        col.RecordArrival(0, 3, info, 0.200, 200000);

        // After opp 2 closes, we should have silence events for (rx=2) and
        // (rx=3) tagged to opp 1's sfn (100000).
        ASSERT_TRUE(CountStatus(col.Events(), 0, 254, 0, /*STATUS_SILENCE=*/2) >= 2);
    }

    // ---------------------------------------------------------------------
    // Test 3: end-of-sim cleanup emits SILENCE for still-open opportunities.
    // ---------------------------------------------------------------------
    {
        auto& col = PsfchKpiCollector::Instance();
        col.ResetForTest();
        std::set<uint32_t> members{0, 1, 2, 3};
        col.RegisterDstL2IdMembers(254, members);

        // Single opp with only RX 1 responding.
        SlHarqInfo info = MakeHarqInfo(0, 254, 0, true);
        col.RecordArrival(0, 1, info, 0.100, 100000);

        // Dump to CSV (which also closes any open opportunities).
        const std::string tmpCsv = "/tmp/psfch_kpi_test_out.csv";
        col.WriteCsv(tmpCsv, /*summaryOnly=*/false);

        // After WriteCsv, the open opp for (sender=0, dst=254, pid=0) should
        // have emitted SILENCE for RX 2 and RX 3.
        const uint32_t sil =
            CountStatus(col.Events(), 0, 254, 0, /*STATUS_SILENCE=*/2);
        ASSERT_TRUE(sil == 2);
    }

    // ---------------------------------------------------------------------
    // Test 4: CastType filtering — Broadcast dstL2Id is skipped.
    // ---------------------------------------------------------------------
    {
        auto& col = PsfchKpiCollector::Instance();
        col.ResetForTest();
        std::set<uint32_t> members{0, 1, 2, 3};
        col.RegisterDstL2IdMembers(/*dstL2Id=*/99, members);
        // Mark dstL2Id=99 as Broadcast.
        RegisterDstL2IdCastType(99, /*castType=*/0); // CAST_BROADCAST

        SlHarqInfo info = MakeHarqInfo(/*txRnti=*/0, /*dstL2Id=*/99, /*pid=*/0, true);
        col.RecordArrival(0, 1, info, 0.100, 100000);

        // Broadcast is filtered out — no events should be recorded.
        ASSERT_TRUE(col.Events().empty());
    }

    // ---------------------------------------------------------------------
    // Test 5: CastType filtering — GroupcastOnlyNack (Option-1) is OUT OF SCOPE
    //         for v1 — events are dropped.
    // ---------------------------------------------------------------------
    {
        auto& col = PsfchKpiCollector::Instance();
        col.ResetForTest();
        std::set<uint32_t> members{0, 1, 2, 3};
        col.RegisterDstL2IdMembers(/*dstL2Id=*/254, members);
        RegisterDstL2IdCastType(254, /*castType=*/3); // CAST_GROUPCAST_NACK

        SlHarqInfo info = MakeHarqInfo(0, 254, 0, /*ack=*/false);
        col.RecordArrival(0, 1, info, 0.100, 100000);

        // GroupcastOnlyNack is out of scope — no events should be recorded.
        ASSERT_TRUE(col.Events().empty());
    }

    std::printf("test_psfch_kpi: ALL PASS\n");
    return 0;
}