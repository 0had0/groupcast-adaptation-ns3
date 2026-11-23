# Paper story (ai-research-writing-skill: story gate)

Thesis: tail members of a platoon lose groupcast packets for two separate reasons,
interference (load, aperiodic traffic) and blockage (buildings on a curve). Leader-only
HARQ fixes the first, a member relay (helper) fixes the second, and a helper that the
leader can take back over (helper NACK + SCI fallback, R6) keeps both gains.

Gap: Kuo et al. (blind helper relaying in reserved slots) and leader-only HARQ studies
evaluate one failure cause at a time. Nobody shows that blind relaying in handed-over
slots wastes the retransmission budget when the helper itself missed the packet.

Contributions:
1. Separation of the two failure causes with ns-3 5G-LENA + SUMO traces (highway vs
   roundabout curve with buildings), 10 seeds, 1440 + 540 runs.
2. Mechanism of "relaying hurts": under saturated aperiodic load the helper decodes
   too few TBs, so the handed retransmission slots stay silent (leader 1.0 tx + helper
   0.65 copies per packet vs 3.0 leader tx for R1).
3. R6: helper named per TB in SCI-2A, silent on success, NACK on failure, leader
   reclaims on NACK or when it hears no helper SCI (fallback).
4. Results: R6 best or tied in all 12 cells; worst member on the curve x2.3 (W periodic sat.);
   fewer resources than leader blind; latency within ~1.5 ms.

Claims to avoid: absolute reliability targets met; generality beyond 9 cars / 30 m IVD /
one selector; congestion control behaviour (not modelled); statement that R4 loss is
collision-dominated (at CBR 0.98 the cause split is not informative).
