# Claim -> evidence (v2, 2026-10-29)

Data: `out/remote_full_v2` (1200 runs) and `out/remote_density_v2` (1080 runs), tag `stale-ndi-fix`, 10 seeds, 95 % t-CI,
paired by seed. Sources: `paper_v2/tex/numbers.json`, `paper_v2/tex/stats_coop_vs.csv` (paired verdicts), the per-arm
caches `paper_v2/analysis/cache/` (`paper_data.py`), `_run.csv` counters (transmissions per packet, takeovers, delay).
The v1 map (pre-fix data, invalid) is in `pre_ndi/`.

| claim (main.tex) | evidence |
|---|---|
| Cooperative: highest mean group PRR in 29 of 30 (scenario, traffic, load) cells; hwy aperiodic 60: Option 2 0.745 vs 0.725, paired -0.02 +/- 0.04 | caches prr; stats_coop_vs R3 prr |
| vs leader Option 2: group PRR better in 28 of 30, tie hwy aperiodic 30 and 60; vs Kuo helper better in 30 | stats_coop_vs R3 / R4 prr |
| best worst-member PRR in all 6 cells at 60 vehicles (Table III) | tab_prr.tex |
| curve periodic 60, worst member: W 0.18 (Opt 2) / 0.37 (Kuo) -> 0.44; E 0.26 / 0.54 -> 0.64 | caches prr_worst |
| time disconnected, curve periodic: leader 0.37-0.45 (W), 0.31-0.38 (E); Opt 2 0.31-0.41; Kuo 0.23-0.32 / 0.16-0.25; coop 0.18-0.23 / 0.10-0.16; HARQ - blind 0.03-0.05 | caches disc_frac |
| time disconnected, hwy: periodic all < 0.14, coop < 0.04; aperiodic Opt 2 0.08 -> 0.41, Kuo 0.11 -> 0.75 (above blind from 15 cars), coop 0.02 / 0.08 at 0 / 15, tie with Opt 2 from 30 | caches disc_frac; stats_coop_vs R3 disc_frac |
| curve aperiodic: Kuo 0.39 (W) / 0.26 (E) at 0 cars, above leader blind from 30 (W) / 45 (E), 0.70 at 60; coop lowest at every load (0.30-0.48 W, 0.19-0.43 E) | caches disc_frac |
| Kuo vs leader blind: curve periodic +0.14..+0.19; hwy aperiodic 60 0.46 vs 0.65 | caches prr |
| DT on the curve: leader mean 0.57-3.8 s, worst member up to 12.2 s; helper schemes 0.31-0.91 s, worst < 2.3 s; hwy 0.20-0.37 s | caches dt_mean / dt_worst |
| DT verdicts on the curve: coop vs Kuo 18 tie / 2 better; vs no takeover 18 tie / 2 worse (rbt ape 60, rbt per 30) | stats_coop_vs dt_mean |
| slots, hwy aperiodic 60: blind 2.94 useful; Kuo 1.57 useful, 1.35 dead (helper missed copy 1); no takeover 1.30 dead; coop 0.30 dead | caches slots (grant-size fix; traced runs `out/remote_slots_trace` change no slot class) |
| Kuo helper copies/pkt hwy aperiodic: 1.70 (0 cars) -> 0.66 (60); leader 1.0 | _run.csv helper_tx, leader_tx / pkts |
| coop hwy aperiodic 60: leader 1.78 + helper 0.78 tx/pkt, 0.54 reclaims + 0.25 fallbacks per pkt; helper SCI missed 10 % vs 1 % at 0 cars | _run.csv leader_tx, helper_tx, helper_reclaim, fallback_tx, helper_sci_missed_rate |
| slots rbt aperiodic 60: Kuo 0.98 dead, coop 0.19; rbtE periodic 0: coop ~0 dead, 0.58 released, Kuo 0.55 redundant | caches slots |
| leader Option 1 dead slots 0.29 / 0.49 / 0.36 (hwy ape 60 / rbt ape 60 / rbtE per 0), all with every lacking member silent; only SCI-2A failed 52 / 26 / 21 %, only SCI-1A missed 28 / 21 / 49 %, mixed rest; no silent member that decoded both SCIs; Option 2 0 dead | caches slots; `paper_notes/round4/option1_silence.py` -> `option1_silence.txt` (traced runs, 240) |
| cost: coop < Opt 2 in 30 / 30 (rbt ape 60: 2.79 vs 3.47); vs Kuo 26 lower, 4 tie; vs no takeover up to +0.20 on aperiodic, equal or lower on periodic | stats_coop_vs res_deliv |
| PSFCH share of coop PRB-symbols 1.2-4.6 % | numbers.json R6.psfch_share |
| Option-1 variant: 0.02-0.23 cheaper, 0.01-0.06 lower group PRR (1 tie) | stats_coop_vs R5 |
| delay: coop mean delay <= 1.54 ms above leader blind; slowest member p95 <= 29.3 ms | _member.csv mean_delay_ms, delay_p95_ms |
| occupancy 0.23 / 0.42 / 0.58 / 0.69 at 15 / 30 / 45 / 60 cars | numbers.json cbr.* |
| DT definition | Rolich, Yildiz, Baiocchi, IEEE Commun. Lett. 30:1860-1864, 2026 (Zotero 6KIRSKVG) |
