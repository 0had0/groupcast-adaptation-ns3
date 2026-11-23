# Plan: SCI format 2-E on air, with a resource-based 2nd-stage SCI error model

Status: plan only, nothing implemented (written 2026-11-20). Validated against **TS 38.212 V17.13.0
(ETSI TS 138 212, 2026-02)** clauses 8.3.1.1, 8.4.1, 8.4.2–8.4.5, and against the code of `ns-3-dev/contrib/nr`
(5G-LENA 321566011 + `patches/nr/local.patch` at HEAD). Items marked **[verify]** were not checked against a spec text.

## Why

Today the helper information travels in a 46-byte simulation-only descriptor that the SCI error model does not charge
(`NrSlSciF2aHeader::GetAirSize()` = 5 bytes). The paper therefore cannot claim the signalling cost of the cooperative
scheme. Goal: carry the helper fields in a real 2nd-stage SCI (format "2-E") and make the SCI-2 decoding probability
depend on its size and on the PSSCH resources it occupies, as in TS 38.212 §8.4.4.

## What the current model does (code, verified)

- `nr-spectrum-phy.cc` ~l. 2929: `m_slErrorModel->GetTbDecodificationStats(sinrPerceived, PSSCH RB bitmap,
  sciF2a.GetAirSize() /*bytes*/, mcs 0, empty history)`; one uniform draw; SCI-2 failure also fails the data.
- Air size = `NrSlSciF2Header::GetSerializedSize()` (4) + 1 = **5 bytes = 40 bits** (real SCI 2-A = 35 bits).
- `NrEesmErrorModel::GetTbBitDecodificationStats`: MCS 0 has ECR 0.12 → LDPC BG2 always; B = A + 24 (CRC);
  lifting size Z_c = first > B/K_b + 0.001; K = 10·Z_c; BLER = step lookup in the largest tabulated CBS ≤ K
  (`nr-eesm-t1.cc`, BG2 MCS 0 CBS list 24…3624). The payload enters **only** through that CBS row:
  payloads 36–41 bits (B = 60–65) all map to CBS 104; 42–47 → 112; 48–56 → 128.
- SINR = EESM over **all PSSCH RBs**; the SCI-2 share of the resources (β_offset, α) is not modelled, so more bits
  never raise the code rate. A larger SCI can even look better (steeper large-CBS curves).
- TB sizing: `NrSlUeMacSchedulerFixedMcs::CalculateTbSize` uses subChSize·nSubCh·symbols with no PSCCH / SCI-2 RE
  subtraction; the scheduler removes a fixed **5 bytes** for SCI-2A (`nr-sl-ue-mac-scheduler-fixed-mcs.cc` l. 653–660
  and l. 1033).
- SCI 1-A: `NrSlSciF1aHeader::SciStage2Format_t` has only `SciFormat2A = 0`, `SciFormat2B = 1`; the MAC always sets
  2A (`nr-sl-ue-mac.cc` l. 2019). No β_offset indicator or sl-Scaling exists anywhere in `contrib/nr`.

## What the standard says (TS 38.212 V17.13.0, verified)

- §8.3.1.1 SCI 1-A: "2nd-stage SCI format – **2 bits**", Table 8.3.1.1-1: `00` 2-A, `01` 2-B, `10` 2-C,
  **`11` Reserved** → SCI 2-E can use code point `11` with no extra bit and no field shift. (The earlier pasted
  layout with a 3-bit format field borrowed from the reserved bits shifts every later field and breaks legacy UEs:
  do not use it.) **Rel-18 caveat (TS 38.212 V18.8.0, verified):** `11` is assigned to **SCI format 2-D**
  (SL positioning: SL PRS resource ID, SL PRS request, 2-bit embedded SCI format + an embedded 2-A/2-B payload,
  Table 8.4.1.4-1, where embedded values `10`/`11` are reserved). From Rel-18 on there is no free code point, so a
  Rel-17 baseline must be stated, or 2-E must reuse a reserved embedded-format value of 2-D or be signalled by
  higher-layer configuration. Comparison figure of all formats: `figure_proposals/fig_messages/fig_sci2_formats.drawio`. Beta_offset indicator – 2 bits, index into `sl-BetaOffsets2ndSCI` (Table 8.3.1.1-2 → TS 38.213
  Table 9.3-2). PSFCH overhead indication 1 bit only if sl-PSFCH-Period = 2 or 4. Reserved = sl-NumReservedBits
  (−1 if sl-IndicationUE-B enabled, then a 1-bit conflict-information receiver flag).
- §8.4.1.1 SCI 2-A: HPN 4, NDI 1, RV 2, Source ID 8, Destination ID 16, HARQ feedback en/dis 1, Cast type 2,
  CSI request 1 = **35 bits**. Cast type `01` = groupcast ACK/NACK (Option 2), `11` = groupcast NACK-only (Option 1).
- §8.4.2 CRC: as §7.3.2, **L_SCI2 = 24**, no scrambling. §8.4.3 channel coding: as §7.3.3 → **Polar code**
  (not LDPC).
- §8.4.4 rate matching:

  Q'_SCI2 = min{ ⌈(O_SCI2 + L_SCI2)·β_offset^SCI2 / (Q_m^SCI2 · R)⌉ , ⌈α · Σ_{l=0}^{N_symb^PSSCH−1} M_sc^SCI2(l)⌉ } + γ

  - O_SCI2 = SCI-2 bits; L_SCI2 = 24; β_offset^SCI2 indicated in SCI 1-A; R = code rate of the SCI 1-A MCS field
    (the PSSCH MCS); α = `sl-Scaling`; γ = vacant REs in the RB of the last coded symbol.
  - M_sc^SCI2(l) = M_sc^PSSCH(l) − M_sc^PSCCH(l) (PSCCH and its DMRS removed);
    N_symb^PSSCH = N_symb^sh − N_symb^PSFCH, N_symb^sh = sl-lengthSymbols − 2;
    N_symb^PSFCH = 3 if sl-PSFCH-Period = 1, 3 or 0 per the PSFCH overhead indication if period 2/4, 0 if period 0.
  - Output G_SCI2 = Q'_SCI2 · Q_m; "a UE is not expected to have G_SCI2 > 4096".
  - Q_m^SCI2 = modulation order of SCI-2 (QPSK, TS 38.211 **[verify clause]**).
- TBS: the REs of SCI-2 are excluded from N_RE in TS 38.214 §8.1.3.2 **[verify]**.

## Implementation steps (to do later)

1. **Header** `model/nr-sl-sci-f2e-header.{h,cc}` (new): derive from `NrSlSciF2Header` (HPN/NDI/RV/src/dst/feedback);
   add relay-member ID, HARQ mode, cast type, CSI request, reserved, with widths from the paper's SCI 2-E figure
   (`figure_proposals/fig_messages/make_fig_sci_bw.py`: 56 payload bits). Expose `GetAirSizeBits()`.
   Add `SciFormat2E = 3` to `SciStage2Format_t` (code point `11`); the MAC sets it for TBs that name a helper;
   receivers deserialize 2-A or 2-E from the SCI 1-A field. Keep the 46-byte descriptor only for simulator
   bookkeeping not on air (slots/subchannels the helper can read from SCI 1-A).
2. **Configuration**: new attributes (e.g. on `NrSlUeMac` / pool config) `SlBetaOffsets2ndSci` (index of Table 9.3-2,
   value **[verify table values in TS 38.213]**), `SlScaling` α ∈ {0.5, 0.65, 0.8, 1} **[verify set in TS 38.331]**;
   carry the β_offset indicator in `NrSlSciF1aHeader`.
3. **Error model** (`nr-spectrum-phy.cc` ~l. 2929), behind `NrSpectrumPhy::Sci2ErrorModel = {Legacy, RateMatched}`
   (default `Legacy` → campaign v3 reproduces bit for bit):
   - compute Q'_SCI2 with the formula above from the PSSCH RBs/symbols of the TB (PSCCH REs removed, PSFCH symbols
     per the period), γ, and α;
   - effective code rate r = (O + L) / (2·Q'_SCI2);
   - BLER: map (r, K) to a curve. **Polar vs LDPC**: SCI-2 is polar-coded but 5G-LENA only has LDPC EESM tables.
     Options, to decide: (a) LDPC proxy — the QPSK MCS of Table 1 whose ECR is closest to r, CBS from O + L
     (call `GetTbBitDecodificationStats` with the exact bit count, no byte rounding); (b) a polar BLER table for
     short blocks (published AWGN curves, e.g. from TR 38.802 / RAN1 evaluations **[verify source]**) used with the
     same EESM effective SINR. (a) is quick and consistent with the PSCCH/SCI-1 model; state it as an approximation.
   - SINR: EESM over the PSSCH RBs (SCI-2 is spread over all PSSCH PRBs from the first PSSCH symbols); optional
     refinement: only the symbols that carry SCI-2.
4. **Data cost**: replace the fixed 5-byte deduction (scheduler l. 653–660, l. 1033) with the Q'_SCI2 REs of the
   format actually sent (and, if added, PSCCH REs), so a larger SCI-2 lowers the TBS / raises the data code rate.
5. **Tests** (`contrib/nr/test`): `Legacy` gives today's BLER for 40 bits; `RateMatched` BLER non-decreasing in O at
   fixed β, α, PSSCH size; Q'_SCI2 matches hand-computed cases (incl. the α cap and γ); 2-A/2-E round-trip through
   SCI 1-A code points `00` / `11`; G_SCI2 ≤ 4096 asserted.
6. **Fair evaluation**: every arm with `RateMatched`; baselines send SCI 2-A (35 bits), the cooperative arms send
   SCI 2-E (56 bits). Targeted campaign: R3, R4_bsc, R6 × 3 routes × 2 traffic × {0, 60} vehicles × 10 seeds
   = 360 runs on ease-rtx (**ask the user before launching**), then compare with v3 (paired by seed).
7. Record everything in `patches/nr/local.patch` and in the reproducibility bundle's provenance.

## Open decisions

- Polar BLER source (step 3, option a or b).
- β_offset and α values (pick one realistic configuration; sweep β later if the SCI-2 cost matters).
- Exact SCI 2-E field list: the paper figure uses a 16-bit relay member ID; a 3-bit member index (N = 8) would keep
  2-E at 43 bits. Decide before step 1.
