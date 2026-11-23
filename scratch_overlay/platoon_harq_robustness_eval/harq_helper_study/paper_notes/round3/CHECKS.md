# Consistency checks (2026-10-27)

Script: `checks.py` (all 2280 runs: full campaign 1200, density 1080). Output:
`checks_summary.csv` (per check: runs, failed) and `checks_fail.csv` (one row per failure).

## Pass everywhere (0 failures)
- leader_tx = new TBs + retransmissions; leader copies per TB <= 3
- used resources = sum of logged transmissions (leader and helper), exactly
- CBR: occupancy <= offered load, <= 1; leader share matches its logged transmissions
- per member: sent = received + lost (by cause); attempts = ok + corrupt + missed
- PRR mean / min and collision loss rate match the member table; packet log matches member rx
- a received packet always has >= 1 attempt; a lost transmitted packet has all attempts failed
- blind arms receive no feedback; Option 1 arms receive no ACK; no feedback arm retransmits without a trigger
- helper counters close (named = decoded + corrupt + SCI missed; corrupt = NACK sent + not sent;
  helper NACK = reclaim + no slot)
- all implementation guards are zero (unmatched PHY records, misaligned slots, relays without decode,
  relays into a reused HARQ process, relays before the leader's retransmission)
- background is identical across schemes for the same scenario and seed

## Failures, with the cause
| check | runs | cause | effect on results |
|---|---|---|---|
| new TBs = packets (and tx per packet) | 209 / 283 | off by exactly 1: a packet near the window edge is counted by generation time, its TB by transmission time | none |
| leader tx = 3 x TBs, blind leader | 44 | off by 1 or 2, same window edge | none |
| leader tx = rows of the log | 5 | off by 1, same window edge | none |
| NACK received <= NACK sent | 820 | `nack_rx` and `explicit_ack_rx` count the whole simulation (warm-up included); `nack_sent` counts the KPI window. Inside the window, received = sent exactly for the leader-only arms (PSFCH is never lost in the model). R4.2 does not log sent feedback at all. | PSFCH overhead in r5r6.py and in the notebook's `psfch_per_tb` is overstated by the warm-up share; recompute from `_ltx.n_nack` in the window |
| copies per TB <= 3 / packet attempts <= 3 | 90 / 179 (R5, R6 only) | sensing fallback false alarm: the leader does not detect the helper's slot-2 copy, takes slot 3, and the helper also sends in slot 3 on the same subchannels. 3 slots are respected but 4 copies are sent; the two slot-3 copies collide with each other | about 1 to 2 % of TBs at 60 cars on the highway (6.9 per run), less elsewhere; a real cost of the fallback, keep in the paper |
| a member decodes the same TB twice | 347 (Option 2 arms) | with "ACK an already decoded TB" on (Option 2), a member that misses the SCI-2A of a later copy forgets that it holds the TB (the copy may carry a new TB) and decodes the next copy again | duplicate delivery only; PRR counts the first; attempt counters include these extra copies (small) |
| same packets in every scheme (aperiodic) | 148 | the leader's aperiodic arrivals come from a random stream that is shifted in the helper arms: leader-only arms share one realisation, helper arms another (spread 3 % mean, 10 % max) | cross-family comparisons (cooperative vs leader-only) under aperiodic traffic are paired by seed and background, not by arrivals. A Welch (unpaired) test gives the same verdict in 132 of 135 comparisons; the 3 that change are borderline (p 0.03 to 0.13) |
| relay after all-ACK | 1 run (R6) | single event | none |
| fallback counters close | 1 run (R5) | single mismatch, not investigated | none |

## Data gap
Kuo + combining (R4_bsc), leader Option 1 (R2), helper + Option 1 (R4.1) and the Option 1 cooperative
variant (R5) were not run in the density campaign: only 0 and 60 cars.
