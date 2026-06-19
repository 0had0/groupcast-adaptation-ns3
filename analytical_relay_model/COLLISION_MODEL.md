# SB-SPS relay collision model

Analytical companion answering the hypothesis:

> *Uncoordinated relay UEs tend to reduce PRR in a platoon of vehicles. This is
> due to collisions that happen when all nodes employ SB-SPS and the traffic
> nature (size, periodicity, PDB).*

It models the probability that the resource a TX node *i* selects for transmission
collides with the resource its relay node *j* (at distance *x*) selects, when every
node runs NR-SL Mode-2 sensing-based SPS (TS 38.214 §8.1.4), and the resulting
group-PRR erosion. It reuses the **same calibrated channel** `g(d,n)=α·log₁₀d+β·n`
and trace geometry as the rest of `analytical_relay_model`.

## Files
- `matlab/collision_model.m` — core: pairwise (`C.pair`) and aggregate (`C.agg`)
  per-resource collision probability given `(x, n)` and overridable knobs
  `{B, PDB, RRI, Tsens, RC, m, mcs}`.
- `matlab/run_collision.m` — **entry point**: sweeps + figures + CSV.
- `matlab/params.m` — adds the SPS/sensing block (`reselCounter`, `Pkeep`,
  `rKeepFrac`, `Tsens_s`, `pSciDecode`, `senseRange_m`, `vRel_mps`,
  `platoonSpacing_m`, `mTx`).

Run: `cd matlab && /Applications/MATLAB_R2025b.app/bin/matlab -batch run_collision`

## Model in one screen

**Resource grid.** A `B`-byte TB at the chosen MCS occupies `c_m=⌈⌈8B′/(η_m R_RE)⌉/W⌉`
sub-channels. The selection window is bounded by **both** the delay budget and the
reservation interval (a grant must fire every RRI): `W=⌊min(PDB,RRI)/T_slot⌋`, of
which a fraction `ρ` carry SL. Orthogonal candidate resources
`M=⌊Wρ⌋·⌊N_sub/c_m⌋`. Two transmitters choosing independently clash with
`p₀=(1/⌊Wρ⌋)·min(1,(2c_m−1)/N_sub)` (same slot × overlapping sub-channels).

**Sensing.** `j` senses (and excludes) `i` when `SNR₀−g(x)` clears the sensing
threshold; with shadowing `p_sense(x)=½erfc((g(x)−θ_sens)/(√2σ))`,
`θ_sens=α·log₁₀(senseRange)`. A short sensing window observes only
`N_obs=⌊T_sens/RRI⌋` periods, so reservations are detected with reliability
`η=1−(1−p_SCI)^{N_obs}` ⇒ effective sensing `p_sEff=p_sense·η`.

**Pairwise collision** (the elementary i↔j event):

```
p_col(x) = (1−p_sEff)·p₀        ← hidden node: pick independently
         + p_sEff·a·p₀          ← sensed: only clash by concurrent reselection
         + f_stale·p₀           ← RC staleness: a sensed neighbour drifts hidden
```
with reselection rate `a=(1−P_keep)/RC` and `f_stale=min(1, v_rel·RC·RRI/spacing)`.

**Aggregate** (one grant vs the other `m−1` transmitters):
`p_agg = 1−(1−p₀)^{K_hid}+ (m−1)a p₀`, `K_hid=(m−1)(1−⟨p_sEff⟩)`. This is the
quantity that multiplies `(1−PRR_link)` and **rises every time an uncoordinated
relay adds a transmitter**.

## What each knob does (figs 7–9)

| Knob | Effect on collision | Mechanism |
|------|--------------------|-----------|
| **distance x** | ↑ (0.3 %→1.2 % over 20→100 m) | sensing fails far out → hidden-node regime |
| **packet size B** | ↑ (step with `c_m`) | bigger TB ⇒ fewer orthogonal resources `M` |
| **PDB** | ↓ | larger delay budget ⇒ wider selection window ⇒ more `M` |
| **RRI** | ↑ as RRI↓ | short period shrinks `min(PDB,RRI)` window **and** raises load `m·(1/RRI)` |
| **sensing window T_sens** | ↓ as T_sens↑ | more observed periods ⇒ reservations detected ⇒ fewer effective hidden nodes |
| **reselection counter RC** | mean ≈ flat, **burst length ∝ RC** | at equilibrium the instantaneous clash prob is `p₀`-set; RC controls correlation (consecutive losses) + a small mobility-staleness term |

## Key numbers (calibrated channel, MCS 16, 800 B, 100 ms PDB/RRI, m=8)
- per-pair clash `p₀ ≈ 3.3 %`, but pairwise `p_col` only **0.26 % @20 m → 1.2 % @100 m**
  because near neighbours sense each other.
- aggregate floor **3.5 % @ m=8 → 5.4 % @ m=12**.
- group PRR over the real IVD geometry: **0.943 (no relay) → 0.929 (3 uncoordinated
  relays, direct link)** — a few-point erosion that the cooperative relay path
  recovers and exceeds (**0.975**) *only if* the relay's own hop is not collision-bound.

**Takeaway.** The model supports the hypothesis *directionally*: each uncoordinated
SB-SPS relay raises the aggregate collision floor and erodes the collision-free
group-PRR ceiling. The magnitude is set by the traffic — it is small at relaxed
settings (large PDB, moderate packet, ≥100 ms RRI) and grows sharply when the
delay budget is tight, the packet is large, or the RRI is short, i.e. exactly the
"traffic nature (size, periodicity, PDB)" called out in the hypothesis. The
benefit of a relay therefore exists only when its cooperative diversity gain
outweighs the contention it injects.

Note: this is a self-contained reduced-form model; it has **not** yet been
cross-validated against an ns-3 SB-SPS collision trace (the PRR(d) channel part
is calibrated, the contention part is first-principles). That validation is the
natural next step.
