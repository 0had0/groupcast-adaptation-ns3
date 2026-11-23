# HANDOFF: results analysis on the v2 data (2026-10-30)

Follows `HANDOFF_STALE_NDI.md` (the bug, the fix, the v2 re-run) and `HANDOFF_PAPER_V2.md` (the paper and the
traced runs). This file covers the analysis of the results themselves, done after the paper was set aside.

## State
- All numbers below are from the v2 data only:
  - `out/remote_full_v2`: 1200 runs, 0 and 60 vehicles, 10 schemes;
  - `out/remote_density_v2`: 1080 runs, 15 / 30 / 45 vehicles, 6 schemes;
  - `out/remote_slots_trace`: 240 traced runs.
  - All have 10 seeds and 95 % paired t-tests by seed.
- Nothing is running and no simulation is needed.
- Nothing is committed: `paper_notes/`, the notebook and `pre_ndi/` are untracked, as before.

Scheme names (code = name used in conversation):

| code | name | what it does |
|---|---|---|
| R1 | Blind | leader, 3 copies |
| R1_bsc | Blind+C | Blind, with soft combining of the copies |
| R2 | Opt 1 | leader HARQ, NACK only |
| R3 | Opt 2 | leader HARQ, ACK / NACK |
| R4 | Kuo | Kuo helper, blind relay |
| R4_bsc | Kuo+C | Kuo, with soft combining |
| R4.1 / R4.2 | NoTk-1 / NoTk-2 | Kuo helper with HARQ Option 1 / 2, no takeover |
| R5 | Coop-1 | cooperative protocol on Option 1 |
| R6 | Coop | proposed cooperative protocol, Option 2: helper silent, NACK takeover, leader fallback |

## Notebook `harq_helper_study/STUDY_RESULTS_remote_full.ipynb` (switched to v2)
- **The problem:** the loader defaulted to `out/remote_full`, the pre-fix campaign, so every figure in it was
  invalid for the helper schemes.
- **The data switch:**
  - The default is now `out/remote_full_v2`, with a header in the intro cell saying so.
  - The whole notebook re-ran with no errors and 34 figures. Spot check: Opt 2, highway, aperiodic, 60
    vehicles, PRR 0.745 = v2.
- **The `_noIR` arms** (HARQ-IR off) were never re-run, so two parts are replaced by notes:
  - the PRR-vs-distance figure with IR on vs off;
  - the HARQ-IR gain section.
- **New section, cells 7-23:** "PRR versus distance on the corrected data (v2), per load level". It reads
  full + density v2. The CBR labels are the measured `cbr_occupied`: 0.01 / 0.23 / 0.42 / 0.58 / 0.69.
  - **Set A**, one figure per scenario x traffic: a panel per load level, a line per method.
  - **Set B**, one figure per scenario x traffic: a panel per method, a line per load level.
  - At 15-45 vehicles only R1, R1_bsc, R3, R4, R4.2 and R6 exist.
- **Spectrum figures:** `../../scripts/plot_spectrum_prr.py` now writes `figures/spectrum_prr_{prb,subch}_remote_full_v2.png`.
  The `*_remote_full.png` files are pre-fix.
- **Pre-fix copy with its old outputs:** `harq_helper_study/notebooks/pre_ndi/STUDY_RESULTS_remote_full.ipynb`.
- **Edited directly:** `../../scripts/build_notebook.py` is out of date with the notebook (different styles, no v2 section).
  Rebuilding from it would lose these changes.
- **Still on pre-fix data, not switched:**
  - `DENSITY_RESULTS.ipynb` (`out/remote_full`, `out/remote_density`);
  - `DENSITY_RESULTS_remote.ipynb` (`out/full`, `out/density`);
  - `REQUESTS.ipynb` (`out/full`; not checked whether that folder is pre-fix).

## Findings

### PRR gain of Coop (paired, `paper_v2/tex/stats_coop_vs.csv`)
- **Group PRR:** best or tied in all 30 cells (3 scenarios x 2 traffic x 5 loads); better than Opt 2 in 28, tie
  in 2.
- **Curve** (roundabout W / E):
  - vs the leader schemes: +0.09 to +0.23, worst member +0.09 to +0.47;
  - vs Kuo: +0.04 to +0.22, growing with load;
  - vs NoTk-2: +0.015 to +0.17;
  - vs Coop-1: +0.02 to +0.06.
- **Highway, periodic:** +0.015 to +0.055. Every scheme is at 0.95 or above.
- **Weak spot, highway, aperiodic, 30-60 vehicles:**
  - It ties Opt 2 (0.725 vs 0.745 at 60, not significant) and Blind+C.
  - Why:
    - no building, so a relay adds little;
    - the helper (member 3 in 97-100 % of packets) misses copy 1 in 67 % of packets;
    - the leader's fallback has to listen through one slot.
  - Members get 2.56 transmissions per packet against Opt 2's 2.89, with 0.30 reserved slots per packet left dead.
  - Coop is cheaper there: 2.16 vs 2.37 subchannel-slots per delivered (packet, member).
- **Coop vs Kuo under interference:** a big win.
  - Highway, aperiodic, 30-60 vehicles: +0.21 to +0.27 group PRR, time disconnected 0.44 vs 0.75.
  - The gain comes from recovering Kuo's dead slots, not from a better relay.

### Spectrum: subchannel-slots transmitted per delivered (packet, member) = `res_deliv`
- **Cheaper than Blind, Blind+C and Opt 2 in 30 / 30 cells, while better in PRR.**
- **vs Kuo:** cheaper in 26, tie in 4.
- **vs Opt 1:** cheaper on the curve, tie on the highway.
- **vs NoTk-2:** cheaper in 6, tie in 11, more expensive in 13 (-0.07 to +0.20). Two opposite effects:
  - **(a) Saves on redundant relays.** NoTk-2's helper sends its own ACK in the same PSFCH occasion; PSFCH is
    half-duplex, so it cannot hear the 7 other ACKs and relays anyway: 0.08-0.36 redundant slots per packet.
    Coop's helper never ACKs, hears all 7 and cancels: 0.04 or less.
  - **(b) Spends on dead slots.** Coop fills the slots NoTk-2 leaves dead. Highway, aperiodic, 60 vehicles:
    12.4 vs 7.6 subchannel-slots per packet, PRR 0.725 vs 0.481, cost per delivered 2.16 vs 1.99 (+8 %).
- **vs Coop-1:** +0.02 to +0.23 in all 12 cells, for +0.01 to +0.06 PRR.
- **Pareto front**, 12 cells at 0 / 60 vehicles:
  - only NoTk-1, Coop-1 and Coop are never beaten on both PRR and cost;
  - plus Opt 2 once (highway, aperiodic, 60) and NoTk-2 once (roundabout E, periodic, 60);
  - Blind, Opt 1, Kuo and both +C variants are dominated everywhere.
- **Caveat:** every scheme announces the same 3-slot grant in SCI-1A. The saving is in what is transmitted, not
  in what is reserved.

### Slot classes (`slot_causes.py` -> `slot_causes_runs.csv`, all 1200 full runs)
- **Unit:** reserved slots per packet (not subchannels; about 3.9 of 5 subchannels per slot on the loaded
  highway).
- **The classes sum exactly to the reserved slots in every run** (max difference 1e-15); reserved is 3, or 2 for
  a few SB-SPS grants.
- **Two copies in the same slot count once.**
- **Definitions** (in the script's docstring):

| class | what it is | split |
|---|---|---|
| useful | a copy sent while a member still lacked the TB | with gain (a lacking member decoded it) / no gain |
| redundant | a copy sent after all 8 had the TB | |
| saved | reserved, empty, all 8 had the TB | |
| dead | reserved, empty, a member still lacked the TB | |

- **Causes:**
  - **useful, no gain:**
    - leader schemes on the curve: 0.7-1.4 slots per packet, nearly all with every lacking member behind
      the building, i.e. the leader repeats from the same blocked spot;
    - helper schemes: cut to 0.25-0.75.
  - **redundant:**
    - Blind: by design;
    - Kuo: blind relay;
    - NoTk-2: PSFCH half-duplex, see above;
    - Option 1 schemes: 0.
    - Opt 2: 0.05 or less, see the next section.
  - **saved:** needs feedback. Leader schemes save 0.6-1.1 on the unloaded highway but only 0.06-0.26 on the
    curve; helper schemes save 0.3-0.58 on the curve.
  - **dead,** only two mechanisms:
    - **(a) Option 1 silence** (Opt 1, NoTk-1, Coop-1): the lacking members missed SCI-1A or failed SCI-2A
      (`option1_silence.py`).
    - **(b) The helper missed copy 1** (Kuo family, little in Coop).
      - Highway: platoon vehicles in the way in 100 % of misses.
      - Roundabout: the helper itself is behind the building in 86 % of misses with no background.
      - Coop's residue (0.30 highway, 0.19 roundabout at 60 vehicles): mostly the fallback's listening slot,
        plus NACKs with no handed slot left.

### Opt 2 redundant copies (`opt2_redundant.py`)
- 728 copies over 45,475 packets (1.6 %), 0.05 slots per packet or less, mostly on the highway.
- In all 728 the previous copy drew no NACK and the leader decided to retransmit.
- On the traced runs, all 64 cases: a member that already had the TB failed the previous copy's SCI-2A, so it sent
  no re-ACK, and Option 2 reads the missing ACK as a NACK.
- This is not a bug: the same rule is why Opt 2 has no dead slots.

### Is the helper choice the problem? (`helper_oracle.py`, geometry proxy)
- **On the curve, mostly yes.**
  - In 43-85 % of packets the last member lacks copy 1 and Kuo's helper cannot serve it.
  - The main reason: the helper decoded copy 1 but has a building between it and the tail (38-58 % of packets
    on the W route, 26-39 % on the E route), more often than missing copy 1.
  - A member that decoded copy 1 and has a clear path to the tail exists in 25-39 % of packets: room for a
    geometry-aware or post-copy-1 choice.
  - Nobody can serve the tail in 8-60 % of packets.
- **This is an upper bound from geometry only (building yes / no, to the tail only), not a simulated scheme.**
- **Highway:** Kuo's member 3 misses copy 1 in 7-15 % of packets without load, 67 % with 60 vehicles.
  Another member decoded copy 1 in 85-100 % of those cases, but those are members 1-2, and the proxy cannot say
  whether they would be better relays.

### Member 1: Blind vs Kuo (`member1_blind_vs_kuo.py`)
- **Equal without load:** PRR 1.000 for both.
- **Highway, aperiodic, 60 vehicles:** 0.986 (Blind) vs 0.880 (Kuo).
- **Roundabout W, aperiodic, 60 vehicles:** 0.959 vs 0.813.
- **Why:**
  - Copy 1 fails at member 1 equally often in both: about 13 % on the highway, 22 % on the roundabout.
  - Blind recovers with 2 more leader copies.
  - In Kuo the helper also missed that copy in 96 % (highway) / 83 % (roundabout) of those packets: the same
    collision or interference hits the front of the platoon. The handed slots then stay dead.
- **Kuo replaces the leader's retransmissions, even for members that never needed a relay.**
- **The helper member's own PRR drops:** member 3 on the loaded highway is at 0.33 (Kuo), 0.34 (NoTk-2), 0.68
  (Coop), 0.80 (Opt 2).

### Disconnection time
- **On the curve, vs the leader schemes:**
  - mean disconnection time 0.57-3.8 s -> 0.33-0.69 s;
  - the worst member's drops by 0.5-10.9 s;
  - time disconnected roughly halves (W periodic: Opt 2 0.37-0.41 -> Coop 0.18-0.23).
- **vs the other helper schemes:**
  - mean disconnection time ties in 18 / 20 cells; NoTk-2 is better in 2 (W route: aperiodic 60, periodic 30).
    Once a member is blocked, any relay ends the outage.
  - Time disconnected is still lower with Coop: vs NoTk-2 by 0.03-0.23, vs Coop-1 by 0.03-0.06. Outages last as
    long, but fewer start.
- **Highway:** about 0.2 s for every scheme, except Kuo under aperiodic load (0.37 s).

## Files (`harq_helper_study/paper_notes/round4/`, all new, untracked)

| file | what it is |
|---|---|
| `slot_causes.py`, `slot_causes_runs.csv` | slot classes with causes, per run, 1200 full runs (~5 min at 8 processes) |
| `opt2_redundant.py` | Opt 2 redundant copies |
| `helper_oracle.py` | Kuo helper vs a choice made after copy 1 |
| `member1_blind_vs_kuo.py` | member-1 loss decomposition |
| `option1_silence.py`, `option1_silence.txt` | Option-1 dead-slot causes (from `HANDOFF_PAPER_V2.md`) |

Run each with `python3 -I <script> <args>`; usage is in each docstring. The out dir is
`harq_helper_study/out`.

## Possible next steps (none started)
1. Switch `DENSITY_RESULTS.ipynb` / `DENSITY_RESULTS_remote.ipynb` (and check `REQUESTS.ipynb`) to v2, or mark
   them pre-fix.
2. Update `../../scripts/build_notebook.py` from the edited notebook, or retire it.
3. A geometry-aware or post-copy-1 helper selection as a new scheme. The oracle says up to about a third of
   curve packets have a better helper available. It needs simulator work and a new campaign.
4. Write the results above into a report or the paper once the paper resumes; the paper is parked
   (`HANDOFF_PAPER_V2.md`).
