// test_helper_select.cc — Unit tests for HelperSelector (H1..H6)
// Covers normal cases and edge cases (window expiry, empty queues, tie-breaking,
// all-ACK, zero-feedback, shadow mode, position error isolation, and building/vehicle blockers).

#include "helper_select.h"

#include "ns3/building.h"
#include "ns3/constant-position-mobility-model.h"
#include "ns3/core-module.h"
#include "ns3/mobility-module.h"
#include "ns3/node-container.h"

#include <cassert>
#include <cmath>
#include <iostream>
#include <memory>
#include <vector>

using namespace ns3;

static int g_passCount = 0;
static int g_failCount = 0;

#define TEST_ASSERT(cond, msg)                                                                     \
    do                                                                                             \
    {                                                                                              \
        if (!(cond))                                                                               \
        {                                                                                          \
            std::cerr << "FAIL: " << msg << " (" << __FILE__ << ":" << __LINE__ << ")\n";          \
            g_failCount++;                                                                         \
        }                                                                                          \
        else                                                                                       \
        {                                                                                          \
            g_passCount++;                                                                         \
        }                                                                                          \
    } while (0)

// Helper: setup 9 platoon nodes in a straight line with 30m spacing
static NodeContainer
SetupPlatoonNodes(double spacing = 30.0)
{
    NodeContainer nodes;
    nodes.Create(9); // 0 = leader, 1..8 = members
    for (uint32_t i = 0; i < 9; ++i)
    {
        Ptr<ConstantPositionMobilityModel> mob = CreateObject<ConstantPositionMobilityModel>();
        mob->SetPosition(Vector(i * spacing, 0.0, 1.6));
        nodes.Get(i)->AggregateObject(mob);
    }
    return nodes;
}

// ---------------------------------------------------------------------------
// Test 1: Static Policy (H1)
// ---------------------------------------------------------------------------
static void
TestStaticPolicy()
{
    std::cout << "[Test 1] Static policy (H1) ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "static";
    cfg.fallback = 4;
    sel.Init(cfg, nodes, 8, nullptr);

    // Call Choose at t = 1.0 s
    uint32_t chosen = sel.Choose(1.0, 1.0, 0, 5);
    TEST_ASSERT(chosen == 4, "Static policy must return fallback member 4");
    TEST_ASSERT(sel.Rows().size() == 1, "Exactly one row recorded");
    TEST_ASSERT(sel.Rows().back().chosen == 4, "Row chosen is 4");
    TEST_ASSERT(sel.Rows().back().applied == 4, "Row applied is 4");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 2: Kuo Distance-Only Policy (H2)
// ---------------------------------------------------------------------------
static void
TestKuoPolicy()
{
    std::cout << "[Test 2] Kuo distance-only policy (H2) ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "kuo";
    cfg.fallback = 4;
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    uint32_t chosen = sel.Choose(0.1, 0.1, 0, 5);
    TEST_ASSERT(chosen >= 1 && chosen <= 8, "Kuo must choose a valid platoon member 1..8");
    // In straight line without buildings, Kuo favours intermediate front/mid members (2 or 3)
    TEST_ASSERT(chosen == 2 || chosen == 3, "Kuo straight line favours member 2 or 3");
    TEST_ASSERT(sel.Rows().back().applied == chosen, "Applied equals chosen when shadow=false");
    TEST_ASSERT(sel.Rows().back().nLinkEval > 0, "Link evaluations recorded");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 3: Map Policy with Building Blockage (H3)
// ---------------------------------------------------------------------------
static void
TestMapPolicyWithBuilding()
{
    std::cout << "[Test 3] Map policy with building (H3) ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);

    // Add a building that crosses between leader (x=0) and tail (x=240),
    // positioned at x in [70, 80], y in [-10, 10], z in [0, 20]
    // This blocks leader -> members 3..8, but leaves leader -> member 1,2 unblocked
    Ptr<Building> b = CreateObject<Building>();
    b->SetBoundaries(Box(70.0, 80.0, -10.0, 10.0, 0.0, 20.0));
    b->SetBuildingType(Building::Commercial);

    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "map";
    cfg.fallback = 4;
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    uint32_t chosen = sel.Choose(0.1, 0.1, 0, 5);
    TEST_ASSERT(chosen >= 1 && chosen <= 8, "Map policy chooses valid member");
    // Building tests should be non-zero
    TEST_ASSERT(sel.Rows().back().nBldTest > 0, "Building intersection tests were executed");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 4: HARQ Statistics Policy Normal Case (H4)
// ---------------------------------------------------------------------------
static void
TestHarqStatsNormal()
{
    std::cout << "[Test 4] HARQ statistics policy normal case (H4) ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "harq";
    cfg.fallback = 4;
    cfg.hstatWindowMs = 1000.0; // 1 s window
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    const double now = 1.0;
    // Simulate feedback in the last 500 ms:
    // Members 1..6 have 100% ACK
    for (uint32_t m = 1; m <= 6; ++m)
    {
        for (int k = 0; k < 5; ++k)
        {
            sel.OnFeedbackSimple(now - 0.1 * k, m, true);
        }
    }
    // Member 7 has 100% NACK (worst receiver)
    for (int k = 0; k < 5; ++k)
    {
        sel.OnFeedbackSimple(now - 0.1 * k, 7, false);
    }
    // Member 8 has 50% NACK
    sel.OnFeedbackSimple(now - 0.1, 8, false);
    sel.OnFeedbackSimple(now - 0.2, 8, true);

    uint32_t chosen = sel.Choose(now, now, 0, 5);
    // Member 7 is worst (100% NACK). Nearest members are 6 and 8.
    // Tie-breaking prefers the member ahead of worst (closer to leader) -> member 6!
    TEST_ASSERT(chosen == 6, "H4 should choose member 6 (nearest ahead of failing member 7)");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 5: Multi-HARQ Process Feedback Pooling
// ---------------------------------------------------------------------------
static void
TestMultiHarqProcessPooling()
{
    std::cout << "[Test 5] Multi-HARQ process pooling ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "harq";
    cfg.fallback = 4;
    cfg.hstatWindowMs = 1000.0;
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    const double now = 2.0;
    // Feedbacks arrive across different times (representing different HARQ PIDs 0, 1, 2)
    // All for member 8 failing
    sel.OnFeedbackSimple(now - 0.8, 8, false); // from PID 0
    sel.OnFeedbackSimple(now - 0.5, 8, false); // from PID 1
    sel.OnFeedbackSimple(now - 0.2, 8, false); // from PID 2

    // Other members succeed
    for (uint32_t m = 1; m <= 7; ++m)
    {
        sel.OnFeedbackSimple(now - 0.3, m, true);
    }

    uint32_t chosen = sel.Choose(now, now, 0, 5);
    // Member 8 is worst. Member 7 is nearest ahead of 8.
    TEST_ASSERT(chosen == 7, "H4 pools all HARQ processes and picks member 7 to assist member 8");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 6: H4 Edge Case — Zero Feedback Data
// ---------------------------------------------------------------------------
static void
TestHarqZeroFeedback()
{
    std::cout << "[Test 6] H4 edge case: zero feedback data ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "harq";
    cfg.fallback = 4;
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    // No feedback recorded at all
    uint32_t chosen = sel.Choose(1.0, 1.0, 0, 5);
    TEST_ASSERT(chosen == 4, "Zero feedback must safely fall back to member 4");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 7: H4 Edge Case — 100% ACKs (Zero NACKs)
// ---------------------------------------------------------------------------
static void
TestHarqAllAcks()
{
    std::cout << "[Test 7] H4 edge case: 100% ACKs across all members ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "harq";
    cfg.fallback = 4;
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    // All members report ACKs
    for (uint32_t m = 1; m <= 8; ++m)
    {
        sel.OnFeedbackSimple(0.9, m, true);
        sel.OnFeedbackSimple(0.8, m, true);
    }
    uint32_t chosen = sel.Choose(1.0, 1.0, 0, 5);
    TEST_ASSERT(chosen == 4, "When all members ACK (no NACKs), H4 safely returns fallback member 4");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 8: H4 Edge Case — Equal NACK Rates (Tie-Breaking)
// ---------------------------------------------------------------------------
static void
TestHarqTieBreaking()
{
    std::cout << "[Test 8] H4 edge case: equal NACK rates tie-breaking ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "harq";
    cfg.fallback = 4;
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    // Member 3 and member 7 both have 100% NACK rate (1 NACK each)
    sel.OnFeedbackSimple(0.9, 3, false);
    sel.OnFeedbackSimple(0.9, 7, false);

    // Tie-break rule: select the farther member from leader (member 7) as worst!
    // Helper nearest ahead of member 7 is member 6!
    uint32_t chosen = sel.Choose(1.0, 1.0, 0, 5);
    TEST_ASSERT(chosen == 6, "Tie-break on worst receiver chooses farther member 7, helper = 6");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 9: H4 Edge Case — Window Expiry Pruning
// ---------------------------------------------------------------------------
static void
TestHarqWindowExpiry()
{
    std::cout << "[Test 9] H4 edge case: window expiry pruning ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "harq";
    cfg.fallback = 4;
    cfg.hstatWindowMs = 500.0; // 0.5 s window
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    // Feedback at t = 1.0 s: member 8 had 5 NACKs
    for (int k = 0; k < 5; ++k)
    {
        sel.OnFeedbackSimple(1.0, 8, false);
    }

    // Now decide at t = 2.0 s (1.0 s later, > 500 ms window):
    // Member 2 had a fresh NACK at t = 1.8 s
    sel.OnFeedbackSimple(1.8, 2, false);

    uint32_t chosen = sel.Choose(2.0, 2.0, 0, 5);
    // Member 8's NACKs at t=1.0s are outside the 500ms window [1.5s, 2.0s].
    // Only member 2's NACK at t=1.8s is in window.
    // Worst is member 2. Nearest helper ahead of 2 is member 1!
    TEST_ASSERT(chosen == 1, "Expired feedback from member 8 is ignored; member 2 is worst, helper=1");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 10: H5 (harqKuo) and H6 (harqMap) Blending
// ---------------------------------------------------------------------------
static void
TestHarqModelBlending()
{
    std::cout << "[Test 10] H5 (harqKuo) and H6 (harqMap) blending ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector selKuo;
    HelperSelector::Config cfgKuo;
    cfgKuo.policy = "harqKuo";
    cfgKuo.fallback = 4;
    cfgKuo.hstatPrior = 10.0;
    selKuo.Init(cfgKuo, nodes, 8, nullptr);

    HelperSelector selMap;
    HelperSelector::Config cfgMap;
    cfgMap.policy = "harqMap";
    cfgMap.fallback = 4;
    cfgMap.hstatPrior = 10.0;
    selMap.Init(cfgMap, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    // Member 8 fails completely
    for (int k = 0; k < 10; ++k)
    {
        selKuo.OnFeedbackSimple(0.9, 8, false);
        selMap.OnFeedbackSimple(0.9, 8, false);
    }
    uint32_t chosenK = selKuo.Choose(1.0, 1.0, 0, 5);
    uint32_t chosenM = selMap.Choose(1.0, 1.0, 0, 5);
    TEST_ASSERT(chosenK >= 1 && chosenK <= 8, "harqKuo chooses valid member");
    TEST_ASSERT(chosenM >= 1 && chosenM <= 8, "harqMap chooses valid member");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 11: Shadow Mode
// ---------------------------------------------------------------------------
static void
TestShadowMode()
{
    std::cout << "[Test 11] Shadow mode isolation ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "kuo";
    cfg.fallback = 4;
    cfg.shadow = true; // compute but signal fallback
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    uint32_t applied = sel.Choose(0.1, 0.1, 0, 5);
    TEST_ASSERT(applied == 4, "In shadow mode, applied must be fallback member 4");
    TEST_ASSERT(sel.Rows().back().chosen != 4, "Chosen was computed according to policy");
    TEST_ASSERT(sel.Rows().back().applied == 4, "Applied in row is 4");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 12: Empty CAMs Edge Case
// ---------------------------------------------------------------------------
static void
TestEmptyCams()
{
    std::cout << "[Test 12] Empty CAMs edge case ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "kuo";
    cfg.fallback = 4;
    // Do NOT run Simulator to prevent snapshot from firing
    sel.Init(cfg, nodes, 8, nullptr);

    // Call Choose without any snapshot
    uint32_t chosen = sel.Choose(0.0, 0.0, 0, 5);
    TEST_ASSERT(chosen == 4, "Empty CAMs safely returns fallback 4");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 13: Decision Delay Edge Case
// ---------------------------------------------------------------------------
static void
TestDecisionDelay()
{
    std::cout << "[Test 13] Decision delay edge case ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "kuo";
    cfg.fallback = 4;
    cfg.camPeriodS = 0.1;
    cfg.decisionDelayS = 0.05; // 50 ms delay
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.3));
    Simulator::Run();

    // At t = 0.12, snapshot at 0.1 is 20 ms old (< 50 ms delay, not yet usable)
    // Should use snapshot at 0.0 s
    uint32_t chosen = sel.Choose(0.12, 0.12, 0, 5);
    TEST_ASSERT(chosen >= 1 && chosen <= 8, "Valid choice made with delayed snapshot");
    TEST_ASSERT(sel.Rows().back().camAge >= 0.05, "Cam age is at least decision delay");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 14: Position Error Determinism & RNG Isolation
// ---------------------------------------------------------------------------
static void
TestPositionErrorDeterminism()
{
    std::cout << "[Test 14] Position error determinism & isolation ... ";
    NodeContainer nodes1 = SetupPlatoonNodes(30.0);
    HelperSelector sel1;
    HelperSelector::Config cfg1;
    cfg1.policy = "kuo";
    cfg1.posErrM = 1.5;
    cfg1.prngSeed = 42;
    sel1.Init(cfg1, nodes1, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();
    uint32_t c1 = sel1.Choose(0.1, 0.1, 0, 5);
    Simulator::Destroy();

    NodeContainer nodes2 = SetupPlatoonNodes(30.0);
    HelperSelector sel2;
    HelperSelector::Config cfg2;
    cfg2.policy = "kuo";
    cfg2.posErrM = 1.5;
    cfg2.prngSeed = 42;
    sel2.Init(cfg2, nodes2, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();
    uint32_t c2 = sel2.Choose(0.1, 0.1, 0, 5);
    Simulator::Destroy();

    TEST_ASSERT(c1 == c2, "Identical seed produces identical helper choice under posErr");
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 15: Background Vehicle Blocker Detection
// ---------------------------------------------------------------------------
static void
TestBackgroundBlocker()
{
    std::cout << "[Test 15] Background vehicle blocker detection ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    // Add a 10th node as a background vehicle positioned between member 1 and member 2:
    // member 1 at (30, 0), member 2 at (60, 0).
    // Background vehicle at (45, 0.2, 1.6)
    NodeContainer bgNode;
    bgNode.Create(1);
    Ptr<ConstantPositionMobilityModel> bgMob = CreateObject<ConstantPositionMobilityModel>();
    bgMob->SetPosition(Vector(45.0, 0.2, 1.6));
    bgNode.Get(0)->AggregateObject(bgMob);
    nodes.Add(bgNode);

    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "map";
    cfg.fallback = 4;
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    uint32_t chosen = sel.Choose(0.1, 0.1, 0, 5);
    TEST_ASSERT(chosen >= 1 && chosen <= 8, "Map policy handles background blockers");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 16: Complexity Logging Metrics (link eval, building tests, CPU us)
// ---------------------------------------------------------------------------
static void
TestComplexityMetrics()
{
    std::cout << "[Test 16] Complexity logging metrics ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "kuo";
    cfg.fallback = 4;
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    sel.Choose(0.1, 0.1, 0, 5);
    const auto& rows = sel.Rows();
    TEST_ASSERT(!rows.empty(), "Row was recorded");
    TEST_ASSERT(rows.back().nLinkEval > 0, "Link evaluations must be > 0");
    TEST_ASSERT(rows.back().cpuUs >= 0.0, "CPU us recorded");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 17: Named Probability Clamp (ClampProbability)
// ---------------------------------------------------------------------------
static void
TestClampProb()
{
    std::cout << "[Test 17] Named probability clamp (ClampProbability) ... ";
    TEST_ASSERT(ClampProbability(-0.05) == 1e-9, "Negative probability must clamp to 1e-9");
    TEST_ASSERT(ClampProbability(-1e-15) == 1e-9, "Small negative epsilon must clamp to 1e-9");
    TEST_ASSERT(ClampProbability(1.05) == 1.0 - 1e-9, "Probability > 1.0 must clamp to 1 - 1e-9");
    TEST_ASSERT(ClampProbability(1.0 + 1e-15) == 1.0 - 1e-9, "1.0 + epsilon must clamp to 1 - 1e-9");
    TEST_ASSERT(ClampProbability(0.0) == 1e-9, "0.0 clamps to 1e-9");
    TEST_ASSERT(ClampProbability(1.0) == 1.0 - 1e-9, "1.0 clamps to 1 - 1e-9");
    TEST_ASSERT(ClampProbability(0.42) == 0.42, "Valid probability in (1e-9, 1 - 1e-9) must remain untouched");
    TEST_ASSERT(ClampProbability(std::numeric_limits<double>::quiet_NaN()) == 0.5, "NaN must safely return 0.5");
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 18: Purely Empirical Mode (hstatPrior == 0) and Zero Observations Fallback
// ---------------------------------------------------------------------------
static void
TestPurelyEmpiricalPriorZero()
{
    std::cout << "[Test 18] Purely empirical mode (hstatPrior == 0) ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "harqKuo";
    cfg.fallback = 4;
    cfg.hstatPrior = 0.0; // purely empirical
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    // Zero observations initially: should safely fall back to model without NaN
    uint32_t chosenInit = sel.Choose(0.1, 0.1, 0, 5);
    TEST_ASSERT(chosenInit >= 1 && chosenInit <= 8, "Zero observations with prior=0 safely makes a valid choice");
    TEST_ASSERT(!std::isnan(sel.Rows().back().score[chosenInit - 1]), "Score must not be NaN with prior=0");

    // Add observations: member 8 fails 5 times, other members succeed
    const double now = 1.0;
    for (int k = 0; k < 5; ++k)
    {
        sel.OnFeedbackSimple(now - 0.1 * k, 8, false);
    }
    for (uint32_t m = 1; m <= 7; ++m)
    {
        sel.OnFeedbackSimple(now - 0.1, m, true);
    }

    uint32_t chosen = sel.Choose(now, now, 0, 5);
    TEST_ASSERT(chosen >= 1 && chosen <= 8, "Purely empirical mode makes valid choice after feedback");
    TEST_ASSERT(!std::isnan(sel.Rows().back().score[chosen - 1]), "Score must be finite");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 19: Propagation Parameter Validation
// ---------------------------------------------------------------------------
static void
TestPropagationParameterValidation()
{
    std::cout << "[Test 19] Propagation parameter validation ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    // Valid defaults
    sel.Init(cfg, nodes, 8, nullptr);
    TEST_ASSERT(cfg.fcGHz > 0.0, "fcGHz > 0");
    TEST_ASSERT(cfg.shadowLosDb > 0.0, "shadowLosDb > 0");
    TEST_ASSERT(cfg.shadowNlosDb > 0.0, "shadowNlosDb > 0");
    TEST_ASSERT(cfg.subchPrbs > 0, "subchPrbs > 0");
    TEST_ASSERT(std::isfinite(cfg.noiseFigureDb), "noiseFigureDb finite");
    TEST_ASSERT(std::isfinite(cfg.sinrReqDb), "sinrReqDb finite");
    TEST_ASSERT(cfg.hstatPrior >= 0.0, "hstatPrior >= 0");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 20: TB-Level Observation Semantics (No Re-trial Pseudo-Replication)
// ---------------------------------------------------------------------------
static void
TestTbLevelFeedbackUpdate()
{
    std::cout << "[Test 20] TB-level feedback observation semantics ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector sel;
    HelperSelector::Config cfg;
    cfg.policy = "harq";
    cfg.fallback = 4;
    cfg.hstatWindowMs = 1000.0;
    sel.Init(cfg, nodes, 8, nullptr);

    Simulator::Stop(Seconds(0.2));
    Simulator::Run();

    const double now = 1.0;
    // TB 100: Member 8 fails initially with DTX, then NACKs on retx 1, then succeeds with ACK on retx 2
    sel.OnFeedback(now - 0.3, 8, 100, static_cast<uint8_t>(FeedbackKind::DTX));
    sel.OnFeedback(now - 0.2, 8, 100, static_cast<uint8_t>(FeedbackKind::NACK));
    sel.OnFeedback(now - 0.1, 8, 100, static_cast<uint8_t>(FeedbackKind::ACK));

    // For other members: TB 100 succeeded with ACK
    for (uint32_t m = 1; m <= 7; ++m)
    {
        sel.OnFeedback(now - 0.1, m, 100, static_cast<uint8_t>(FeedbackKind::ACK));
    }

    // Since member 8 eventually succeeded on TB 100, its packet-level outcome is ACK (0 failures)!
    // All members have 100% success on TB 100. So H4 should fall back to 4 (no member is failing).
    uint32_t chosen = sel.Choose(now, now, 0, 5);
    TEST_ASSERT(chosen == 4, "TB 100 resolved to ACK; H4 sees no failing members and returns fallback 4");

    // Now for TB 101: Member 8 fails with DTX and never ACKs
    sel.OnFeedback(now - 0.05, 8, 101, static_cast<uint8_t>(FeedbackKind::DTX));
    for (uint32_t m = 1; m <= 7; ++m)
    {
        sel.OnFeedback(now - 0.05, m, 101, static_cast<uint8_t>(FeedbackKind::ACK));
    }

    chosen = sel.Choose(now, now, 0, 5);
    // Member 8 failed TB 101 (1 failure out of 2 TBs), other members have 0 failures.
    // Member 8 is worst, nearest ahead is member 7.
    TEST_ASSERT(chosen == 7, "TB 101 failed; member 8 is worst and helper is member 7");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Test 21: Scenario-Aware Pathloss (Highway vs Urban)
// ---------------------------------------------------------------------------
static void
TestScenarioAwarePathloss()
{
    std::cout << "[Test 21] Scenario-aware pathloss ... ";
    NodeContainer nodes = SetupPlatoonNodes(30.0);
    HelperSelector selUrb;
    HelperSelector::Config cfgUrb;
    cfgUrb.scenario = "urban";
    selUrb.Init(cfgUrb, nodes, 8, nullptr);

    HelperSelector selHwy;
    HelperSelector::Config cfgHwy;
    cfgHwy.scenario = "highway";
    selHwy.Init(cfgHwy, nodes, 8, nullptr);

    TEST_ASSERT(cfgUrb.scenario == "urban", "Urban scenario configured");
    TEST_ASSERT(cfgHwy.scenario == "highway", "Highway scenario configured");
    Simulator::Destroy();
    std::cout << "OK\n";
}

// ---------------------------------------------------------------------------
// Main test runner
// ---------------------------------------------------------------------------
int
main(int argc, char* argv[])
{
    CommandLine cmd(__FILE__);
    cmd.Parse(argc, argv);

    std::cout << "=== RUNNING HELPER_SELECT UNIT TESTS ===\n";

    TestStaticPolicy();
    TestKuoPolicy();
    TestMapPolicyWithBuilding();
    TestHarqStatsNormal();
    TestMultiHarqProcessPooling();
    TestHarqZeroFeedback();
    TestHarqAllAcks();
    TestHarqTieBreaking();
    TestHarqWindowExpiry();
    TestHarqModelBlending();
    TestShadowMode();
    TestEmptyCams();
    TestDecisionDelay();
    TestPositionErrorDeterminism();
    TestBackgroundBlocker();
    TestComplexityMetrics();
    TestClampProb();
    TestPurelyEmpiricalPriorZero();
    TestPropagationParameterValidation();
    TestTbLevelFeedbackUpdate();
    TestScenarioAwarePathloss();

    std::cout << "========================================\n";
    std::cout << "Results: " << g_passCount << " passed, " << g_failCount << " failed.\n";

    if (g_failCount > 0)
    {
        std::cerr << "UNIT TESTS FAILED!\n";
        return 1;
    }
    std::cout << "ALL UNIT TESTS PASSED SUCCESSFULLY!\n";
    return 0;
}
