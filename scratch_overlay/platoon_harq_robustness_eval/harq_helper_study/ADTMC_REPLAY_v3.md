# ADTMC validation, step 1: deterministic protocol replay (campaign v3, 0 background cars)

Model under test: `adtmc_rewrite/adtmc.py` (external package, reviewed, not executed): `helper_branches` +
`Branch.active`, three opportunities, fixed helper, Option-2 stop test "every expected member ACKs the copy".
Replay: `scripts/adtmc_replay.py` (reads only the raw v3 logs). Outputs: `out/adtmc_replay_v3/` (`tb_none.csv` one row per TB,
`summary_by_arm.csv`, `summary_by_cell.csv`, `mismatch_patterns.csv`).

Scope: R3, R4.2, R6; every 0-car run (3 scenarios × 2 traffic classes × 10 seeds = 180 runs, 66,518 TBs in the KPI
windows). No probability is fitted. The replay checks whether, given what each receiver observably did, the model's
rules predict the copies the simulator actually sent.

## What is observable in the v3 logs

| quantity | source | observable? |
|---|---|---|
| each copy's transmitter and kind (L1, leader retransmission, takeover after helper NACK T1, fallback T2, helper copy H) | `_att`, `_ltx` origin, `_htx` | yes, in order. **Not** which reserved slot was left empty. |
| member outcome on a copy: D (decoded) / N (failed, NACK emitted) / M (no usable control) | `_att` status + `_nack` emission within 3 ms | yes for members still lacking the TB. Status 2 without a NACK = failed SCI-2A → M. |
| ACK from a member that already had the TB | — | **no** (`_att` logs status 0). Shown indirectly: in R3, when every member lacking after copy 1 decodes copy 2, the leader stops after copy 2 in 4,336 of 4,765 TBs (91 %). So decoded members do keep ACKing, as the model assumes. |
| leader's stop decision after each of its own copies | `_ltx` decision | yes |
| helper's stop decision after its own copy | — | **no**. But if an expected member still lacks the TB after copy 2, the all-ACK test must fail: that case is checked strictly. Only when every expected member has the TB are both continuations accepted (`free`). |

## Results

| arm | TBs | actions as the model predicts | mismatches | decisions not observable (`free`) |
|---|---|---|---|---|
| R3 | 22,151 | **100.00 %** | 0 | 0 % |
| R4.2 | 22,184 | **99.50 %** | 111 | 58.7 % |
| R6 | 22,183 | **99.81 %** | 42 | 42.4 % |

The model's first stop test, computed from the member outcomes of copy 1, equals the leader's logged decision in
every R3 and R6 TB. In R4.2 the leader also waits for the helper's ACK: 315 TBs where the helper missed copy 1's SCI
fail the leader's test but would pass the model's test that leaves the helper out. That doesn't change any R4.2
action, since R4.2's leader has no slot left. 1 R4.2 TB is unexplained.

### Mismatches

1. **Fallback false alarm (R6, 28 TBs: `L1-H-T2`, `L1-H-T2-H`).** The helper relayed, the leader did not decode the
   helper's SCI in the handed slot and retransmitted anyway; in 5 TBs the helper then sent a further copy (4 copies
   in total). The model assumes perfect source sensing of helper transmissions. Rare at 0 cars (0.13 % of R6 TBs);
   across all loads the v3 consistency checks flag 357 R5 / R6 runs with at least one 4-copy TB
   (`paper_notes/round3_v3/checks_summary.csv`).
2. **Helper stops although a member still lacks the TB (R4.2 111 TBs, R6 14 TBs; roundabouts only; `L1-H`
   observed, `L1-H-H` predicted).** In every case the lacking member (1 or 2 per TB) **missed the SCI of the
   helper's copy** (status 0, so no feedback), never decoded, and lost the packet (`_pkt` rx = 0, 142 pairs). The
   simulator's rule (`nr-sl-ue-mac.cc:1699`, `HelperFeedbackMode = 2`, `HelperGroupSize = 7`) cancels only when
   ≥ 7 distinct members have ACKed and none NACKed, so a silent member should block the cancellation. The run
   counters show no "too late", "no HARQ process" or "no resource" cancellation, and the helper's own record of the
   copy says "keep retransmitting" (decision 0). The gap from the leader's copy to the helper's copy (median 11
   slots) is the same as in TBs that do get two helper copies, so this is not an unused early slot. **Unresolved
   from these logs:** they record neither the ACKs the helper received nor its cancellation reason per TB. It needs
   a traced re-run (below). Whatever the cause, the model overestimates helper copies, and the delivery of these
   members, in this case.

### Not tested by this replay
- the position of an empty opportunity (the model's fallback at opportunity 3);
- feedback **reception** by the actors (only emission is logged), hence feedback loss (PSFCH beyond `MaxLossDb`);
- the helper's stop decision whenever every expected member already had the TB (42–59 % of helper-arm TBs);
- runs with background traffic, where reservation (2-slot grants), collisions and the fallback false alarm are more
  frequent. At 0 cars every leader grant reserved 3 slots.

## Implications for the model
- The protocol branches of section 4 of the model description hold for the leader in every observable case, and for
  the helper except mismatch 2. The structure does not need changing before fitting, with two caveats: imperfect
  source sensing (mismatch 1) and the helper's cancellation with a silent member (mismatch 2).
- Both mismatches are rare at 0 cars. Neither changes the paired-gain picture, but both bias cost and delivery
  predictions slightly (mismatch 1: an extra copy; mismatch 2: a missing copy).

## Next steps (model guide, steps 4–5)
1. One representative traced cell (e.g. roundabout W, aperiodic, R4.2 and R6, 0 cars, seeds 1–2) with
   `--writeHarqTrace=true` plus the MAC cooperative-relay trace, to log per TB the ACKs the helper overheard and why
   it cancelled. That resolves mismatch 2 and gives SCI-1 / SCI-2 / data outcomes separately for fitting.
2. Repeat the replay at 60 cars (same script, `--bg moderate`) to measure the fallback false alarm and 2-slot grants
   under load.
3. Then fit the reception kernels on seeds 1–7 and predict seeds 8–10 (steps 7–9).
