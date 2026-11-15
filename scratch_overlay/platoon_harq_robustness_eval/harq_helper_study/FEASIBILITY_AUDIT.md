# Technical audit: cooperative groupcast HARQ (ns-3 / 5G-LENA)

Read-only audit, 2026-11-15. No code was changed and no simulation was run. Numbers marked "log analysis" were
computed in this audit from existing campaign-v3 outputs (`out/remote_v3/<arm>/*`, 10 seeds, 0 and 60 background
vehicles).

**Manuscript audited.** `coop-harq-analytical/paper/evaluation/evaluation.tex`, the section that contains the "4 to
12 % fewer subchannel-slots" claim. `harq_helper_study/paper/main.tex` is an older full draft built on campaign v2
data (`out/remote_full_v2`). Its numbers predate v3 and must not be reused.

**Paths.** Paths are relative to the ns-3 repository root unless a path names another repository.
- `NR/` = `ns-3-dev/contrib/nr/model/`.
- `PL/` = `scratch_overlay/platoon_harq_robustness_eval/`.
- `ST/` = `scratch_overlay/common/stack/`.
- `AN/` = `~/Documents/coop-harq-analytical/`.

**Arm names.** The paper schemes map to code arms as follows (`PL/harq_helper_study/study.py:37-52`):

| Paper scheme | Code arm |
|---|---|
| Blind | R1 |
| Blind + C | R1_bsc |
| Option 1 | R2 |
| Option 2 | R3 |
| Helper blind + C | R4_bsc |
| No takeover | R4.2 |
| Cooperative | R6 |
| Cooperative (Option 1) | R5 |

All helper arms use the same flags: `--helperSched=S1 --helperSelect=kuo --helper=4`.

**Code versions.**
- ns-3: 3.42, branch `v2x-lte-dev`, commit c90c13b831 (tag ns-3-dev-v2x-v1.1).
- 5G-LENA: `nr-v2x-dev` at 32156601 (`v2x-1.1-8-g32156601`).
- Local NR changes: 15 modified files, +2761 / -199 lines.
- `patches/nr/local.patch` is identical to `git -C ns-3-dev/contrib/nr diff HEAD` once index lines are ignored.

---

## 1. MAC state machine and retransmission ownership

**Three layers do the work.** Keeping them separate answers the "selection vs. trigger" concern.

| Role | Where | What it does |
|---|---|---|
| Resource selection and reservation | `ST/nr_sl_coop_scheduler.cc` (leader only) | Picks up to K = 3 slots per TB with the stock Mode-2 sensing candidates. |
| Ownership of each reserved slot | `NrSlGrant::usurpedByHelper` (`NR/nr-sl-ue-mac.h:94`) | Set by the scheduler when the grant is published. Read and cleared by the leader MAC. |
| HARQ retransmission trigger (Option 2) | `PL/nack_only_harq.cc` (scenario-level controller) | Not in the MAC. It wraps the leader's PHY-to-MAC SAP. |

### Q1. Which functions select and reserve resources?

- **Candidate set.** `NrSlUeMac::GetCandidateResources` builds the candidates (stock sensing). The study turns on
  two extensions, `SlDynExclusion` and `SlProjectAnnounced` (`NR/nr-sl-ue-mac.cc:96-115`).
- **Slot choice.**
  - `NrSlCoopScheduler::DoNrSlAllocation` (`ST/nr_sl_coop_scheduler.cc:1976`) branches at line 1987.
  - HARQ-enabled grants with a PSFCH period > 0 go to `SelectResourcesWithConstraint` (2157).
  - That function calls `IsCandidateResourceEligible` (2221) and `IsMinTimeGapSatisfied` (2205-2218). Together they
    require each pair of consecutive reserved slots to be at least (first PSFCH slot ≥ s + MinTimeGapPsfch) +
    MinTimeGapProcessing apart. This is stock 5G-LENA logic, copied into the coop scheduler.
  - Blind grants use `SelectResourcesForBlindRetransmissions` (2072-2100). With a helper, this redraws up to 50
    times until the first handed slot is at least `HelperMinGap` = 2 slots after the leader's last own slot. After 50
    failures it keeps a non-compliant selection (2097-2099).
- **RV per slot.** `GetRv` (1913) assigns RV 0, 2, 3, 1.
- **SCI 1-A.** With `--sciOnEveryTx=true` (v3), each slot's SCI 1-A carries `slotNumInd = min(3, remaining)`
  (`DoNrSlAllocation`, lines ≈2024-2031). The first SCI 1-A therefore announces all three slots, including the slots
  later handed to the helper.

### Q2. How do the resources reach the helper?

- **Marking.** `CheckForGrantsToPublish` (`ST/nr_sl_coop_scheduler.cc:1697-1712`) marks every slot after the first
  `LeaderTxCount` = 1 slot as `usurpedByHelper[slot] = true`. It then sets `grant.helperId = HelperFor(...)`, the
  per-TB Kuo choice, and publishes the grant with `SchedNrSlConfigInd` (1714).
- **SCI-2A side information.** The leader MAC writes it at `NR/nr-sl-ue-mac.cc:1888-1956`:
  - `helperId`;
  - `relaySlot[0..1]` and `relaySbStart[0..1]` of the later usurped slots;
  - `sbLength`, `mcs`, `tbSize`, and the PSCCH/PSSCH symbol layout.
- **Helper side.** `DoReceivePsschPhyPdu` (`NR/nr-sl-ue-mac.cc:1395-1517`, `ownRes == 0` branch):
  - The helper builds a relay `NrSlGrant` from these fields: `ndi = 0`, `rv = 2`, `slotNumInd = 1`
    (lines 1405-1446).
  - It copies the decoded MAC PDUs and rewrites their RNTI tag (1458-1469).
  - It allocates a local HARQ process and forces the TB into it (`ForceHarqIdForRelay`, 1497).
- **Not 3GPP signalling.** The handover uses up to 46 bytes of extra SCI-2A fields. The SCI-2A error model counts
  only the stock air size (`NrSlSciF2aHeader::GetAirSize`, `NR/nr-sl-sci-f2a-header.cc:112-115`; extra bytes at
  124-130).
  - The slot and subchannel fields duplicate what the SCI 1-A TRIV/FRIV already announce.
  - The helper identity has no 3GPP field.
  - The handover of a reserved resource from one UE to another is not a 3GPP procedure.

### Q3. Where is ownership of each opportunity stored?

- **Leader.** `NrSlGrant::usurpedByHelper` (map slot → bool) in the leader's `m_slGrants`.
- **Helper.** The helper keeps no ownership flag. It holds a separate relay grant for the same slots.
- **Reading the flag.** `DoNrSlSlotIndication` reads it at `NR/nr-sl-ue-mac.cc:1690-1693`. In an usurped slot the
  leader:
  - advances its HARQ counters;
  - sends no PSSCH (1744, 1781), no SCI and no PSCCH (1826-1832);
  - adds nothing to its transmit history, so it can sense the slot.
- **Takeover.** Ownership changes only through `ReclaimSlHelperSlot` (2755-2784). It flips the first remaining
  usurped slot, in time order, back to the leader.

### Q4. How does the helper decide whether to retransmit?

- **Mode.** In R6, `HelperFeedbackMode = 2` (allAck) and `HelperGroupSize = numMembers - 1 = 7`
  (`PL/platoon_harq_robustness_eval.cc:2155-2169`).
- **Waiting state.** The relay grant starts with `relayNackGated = true`, `relayAwaitRnti` = the leader's RNTI and
  `relayNackSeen = false` (`NR/nr-sl-ue-mac.cc:1474-1480`).
- **Feedback.** `DoReceivePsfch` (2846-2873):
  - adds the RNTI of each overheard ACK on the leader's HARQ id to `relayOverheardAcks`;
  - sets `relayNackSeen` on any NACK.
- **Decision.** At the start of each relay slot, the relay is cancelled if ≥ 7 distinct ACKs and no NACK were
  overheard (1699-1706). In that case the whole relay grant is erased, together with any later relay slot, and
  `COOP_RELAY_NO_NACK` is traced (1712-1725).
- **Second relay slot.** After the helper sends a copy, it re-arms on feedback to its own copy:
  `relayAwaitRnti = GetRnti()`, and the counters are cleared (1798-1806).

### Q5. Does the helper monitor every member's PSFCH?

Yes, in an idealized way:
- `NrSpectrumPhy::RxSlPsfch` (`NR/nr-spectrum-phy.cc:3345-3363`) forwards every feedback message in every PSFCH
  signal the UE receives. It applies no error model, no SINR check, no PSFCH resource mapping and no collision
  between PSFCH transmissions.
- The helper counts any received ACK of the leader's TB.
- PSFCH is lost only in two cases:
  - the signal falls below `MaxLossDb` = 150 dB;
  - the receiver is itself transmitting in that symbol (half-duplex state machine, `StartTxSlFeedback` 2037-2075).

### Q6. How does the source process a helper NACK?

- **The MAC is bypassed.** In Option 2 the leader MAC never sees real PSFCH. `UserWrap::ReceivePsfch`
  (`PL/nack_only_harq.cc:108-115`) passes feedback to the controller only.
- **Controller handling.** `NackOnlyHarqController::OnPsfch` (375-463):
  - It binds the feedback to the pending transmission whose decision falls in this PSFCH occasion.
  - It recognizes the named helper by PHY node id (`nodeId == best->helperNode`). This stands in for the helper's
    own Option-2 PSFCH resource.
  - It calls `ReclaimSlHelperSlot` (436). If no handed slot is left, it counts `helper_nack_no_slot` instead.
- **Stopping retransmissions.** On all-ACK, `Decide` (484-578) injects one synthetic ACK per member into the MAC
  through `DeliverSuccess` (466-481). This is how the controller cancels the remaining retransmissions.

### Q7. How does the source detect a silent helper?

- **When a check is scheduled.** `MaybeScheduleFallback` (`PL/nack_only_harq.cc:610-641`) runs only after a
  non-all-ACK decision, and only if no helper NACK was already seen. It schedules
  `CheckHelperRelay` at the end of the next handed slot plus half a symbol (634).
- **What it listens for.** `OnLeaderPscch` (593-607) records every PSCCH the leader decodes without error, by
  transmitter RNTI and slot (trace `RxPscchTraceUe`).
- **The check.** If no SCI from the helper's RNTI was decoded in that slot, `CheckHelperRelay` (644-667) reclaims
  the following handed slot and counts `fallback_tx`.

### Q8. Can the source tell a silent failed helper from a helper that decoded but chose not to transmit?

No. The source has no explicit signal for this.
- **Why it mostly does not matter.** A helper withholds its relay only after it has overheard all 7 ACKs and no
  NACK. That is the same condition under which the leader decides all-ACK and schedules no check.
- **When the two views can differ.** PSFCH reception is asymmetric when a signal is out of range or a receiver is in
  half-duplex.
  - The helper heard all ACKs but the leader missed one: the leader reclaims slot 3 and sends a redundant copy. No
    collision, because the helper's relay grant was erased.
  - The helper did transmit but the leader failed to decode its PSCCH: the leader also transmits, and the two
    collide (Q9).

### Q9. When can the source and helper transmit simultaneously?

**Mechanism.** A false fallback:
1. The helper relays in slot 2.
2. The leader fails to decode the helper's PSCCH in slot 2.
3. The leader reclaims slot 3.
4. The helper also relays in slot 3 because a member NACKed its slot-2 copy.

Both then send the same TB with the same HARQ id on the same subchannels.

**Log analysis** (R6, `_ltx`/`_htx` matched on slot, HARQ id and RB overlap):
- Every coincident pair was a leader fallback transmission (`origin = 2`).
- In every case the helper had transmitted in the slot the leader checked.
- The leader was never transmitting in that slot, so half-duplex is not the cause: its PSCCH decoding failed.

| Route, traffic | 0 vehicles: fallbacks / coincident | 60 vehicles: fallbacks / coincident (share) |
|---|---|---|
| Highway, aperiodic | 60 / 2 | 1042 / 207 (19.9 %) |
| Highway, periodic | 58 / 0 | 269 / 53 (19.7 %) |
| Roundabout W, aperiodic | 44 / 2 | 686 / 67 (9.8 %) |
| Roundabout W, periodic | 16 / 0 | 143 / 16 (11.2 %) |
| Roundabout E, aperiodic | 31 / 0 | 827 / 127 (15.4 %) |
| Roundabout E, periodic | 10 / 1 | 144 / 13 (9.0 %) |

Highway, aperiodic, 60 vehicles: 207 events over 4945 packets, about 4.2 % of TBs. Test C04
(`PL/tests/results/C04_relay_combining.json`, 2026-10-27) already flagged the same pattern: "copy slots coincide
with a leader PSSCH". The manuscript does not mention it.

**Other cases, by code reading.**
- **Takeover:** cannot collide. The helper NACKed, so it holds no TB.
- **After a takeover:** the helper may decode the leader's slot-2 copy and relay in slot 3, which is still usurped.
  The leader does not transmit there unless a new fallback check reclaims it.

### Q10. What happens when SCI or PSFCH is lost?

**Member side** (`NR/nr-spectrum-phy.cc`):
- **SCI 1-A lost.** The TB is not expected and the member sends no feedback. The leader reads DTX as NACK
  (Decide, Option 2).
- **SCI-2A lost.** The copy is dropped with no feedback (2953-3005, `SCI2_FAIL`).
- **SCI 1-A on every PSSCH (v3).** A member that missed copy 1 can still decode the later copies. The NDI-bit flush
  in `SlNdiNewTb` (2567-2585) handles this case.

**Helper side:**
- **Missed SCI 1-A or SCI-2A.** No relay grant and no NACK: the helper is silent, and only the fallback recovers.
- **Forced miss for experiments.** `SlHelperMissProb` and `SlHelperMissMode` (2925-2950) inject this case.

**Leader side:**
- **Missed helper PSCCH.** False fallback (Q9).

**PSFCH loss.** Not modelled in ns-3, beyond range and half-duplex (Q5). It is studied only in the model: "PSFCH loss
40 %" row in `AN/paper/evaluation/tables/tab_sensitivity.tex`.

### State-transition table (as implemented, R6)

| Actor | State | Event | Action | Next state | Code |
|---|---|---|---|---|---|
| Source | grant published (slot 1 own; slots 2, 3 usurped) | slot 1 | PSSCH + SCI 1-A (announces 1-3) + SCI-2A (`helperId`, relay slots) | AWAIT_FB(1) | sched 1697-1712; MAC 1888-1956 |
| Source | AWAIT_FB(k) | helper NACK on copy k | reclaim next usurped slot | OWN(next) | nack_only 412-444; MAC 2755 |
| Source | AWAIT_FB(k) | Decide: all 7 non-helper ACK, no NACK | synthetic ACKs; grant erased | DONE | nack_only 519-577, 466 |
| Source | AWAIT_FB(k) | Decide: not all-ACK, no helper NACK | schedule PSCCH check at end of next handed slot | CHECK(h) | 610-641 |
| Source | CHECK(h) | helper SCI decoded in h | none | wait for Decide on its own copies only (none for helper copies) | 644-655 |
| Source | CHECK(h) | no helper SCI in h | reclaim next usurped slot | OWN(next) or NO_SLOT | 658-666 |
| Source | usurped slot | slot start | silent; HARQ counter++ | - | MAC 1690-1693, 1744, 1826 |
| Helper | idle | decodes copy with `helperId` = self | relay grant for listed slots (RV 2, NDI 0) | GATED | MAC 1395-1517 |
| Helper | idle | SCI-2A decoded, TB failed | NACK (ACK suppressed only on success) | idle | shim `PL/nack_only_harq.cc` ≈680-720 |
| Helper | GATED | ≥ 7 ACK and no NACK by slot start | cancel all remaining relay slots | DONE | MAC 1699-1725 |
| Helper | GATED | otherwise at slot start | relay, re-arm on own copy's feedback | GATED(next) | MAC 1792-1806 |
| Member | - | copy decoded | ACK (also ACK repeated copies, `SlAckPrevDecodedTb`) | - | spectrum-phy 2805-2833, 3300-3325 |
| Member | - | SCI-2A or TB fail | NACK if SCI-2A ok; nothing if SCI lost | - | as above |

---

## 2. HARQ feedback and retransmission timing

### Configuration

| Item | Value | Where |
|---|---|---|
| Numerology | μ = 1: 30 kHz SCS, 0.5 ms slot, symbol ≈ 35.7 µs | `PL/platoon_harq_robustness_eval.cc:1424`; stack `sp.mu` |
| Sidelink slots | every slot (`--dedicatedCarrier=true`) | `PL/run_retx.py:30-38`; `ST/nr_sl_stack.cc:352-358` |
| PSFCH period | 1 slot (v3; `psfch` profile adds 2 and 4) | `PL/platoon_harq_robustness_eval.cc:1430`; `ST/nr_sl_stack.cc:393` |
| PSSCH-to-PSFCH gap | `SlMinTimeGapPsfch` = 3 slots (literal) | `ST/nr_sl_stack.cc:394` |
| Processing gap | `MinTimeGapProcessing` = 2 slots (MAC attribute default, not set by the study) | `NR/nr-sl-ue-mac.cc:151-155` |

**Feedback reaching the MAC.**
- The PSFCH is in symbol 12 of slot m+3 for a PSSCH in slot m.
- `NrUePhy::PhyPsfchReceived` delays delivery by one slot (`NR/nr-ue-phy.cc:2125-2136`, comment: "XXX
  provisional value of 1 slot"). Feedback therefore reaches the MAC and controller at about (m+4) + 13 symbols.

**Processing and preparation.**
- The source's decision is a timer at (m+4) + 13.5 symbols (`PL/nack_only_harq.cc`, `decideAt`, ≈360-370). It runs
  in zero simulated time.
- The MAC builds slot n at the start of slot n (`NrUePhy::StartSlot`, `NR/nr-ue-phy.cc:737-744`). There is no PHY
  preparation lead time.
- The helper's relay grant is created when the PSSCH is decoded at the end of slot m, also in zero simulated time.

**Gaps between reserved slots.** Enforced for every HARQ-enabled grant (R2, R3, R4.1, R4.2, R5, R6):
s(k+1) ≥ PSFCH(s(k)) + 2, which equals s(k) + 5 with period 1.

### Takeover sequence (period 1)

| Time | Event |
|---|---|
| m | Copy 1 |
| m+3, symbol 12 | Helper NACK on PSFCH |
| (m+4) + 13 sym | NACK reaches the controller; reclaim |
| (m+4) + 13.5 sym | Decide |
| ≥ m+5 | Handed slot; the source transmits |

- The margin between reclaim and the slot start is about one symbol in simulated time.
- It holds by construction, because the selection enforces the 3 + 2 slot gap.
- Measured (log analysis): every takeover transmission was ≥ 5 slots after the copy it answered (min 5, n = 7413).
- Option 2 (R3): every inter-copy gap was ≥ 5 slots (n = 84227).

### Fallback sequence

| Time | Event |
|---|---|
| h ≥ m+5 | Helper's handed slot |
| (h+1) + 0.5 sym | PSCCH check |
| ≥ h+5 | Next handed slot (guaranteed by the gap) |

- Margin: about four slots.
- Measured: fallback transmissions were ≥ 5 slots after the helper slot they checked (min 5, n = 789).
- **Precondition.** The decision on copy 1 is taken at (m+4) + 13.5 sym, before slot h ≥ m+5 starts, so the check
  is always scheduled in time.

### Conclusion on timing

- **Enforced in selection.** The 3GPP PSSCH → PSFCH → processing gap (`sl-MinTimeGapPsfch` + processing) is
  enforced for every reservation of the HARQ arms. Both the takeover and the fallback meet their deadlines in the
  simulator's own timeline.
- **Idealized.** The timeline is idealized in four ways:
  - MAC and controller processing take zero time;
  - SCI detection is instant at the end of the slot;
  - retransmission preparation takes zero time;
  - PSFCH decoding latency is a provisional one slot.
- The configured 2-slot processing gap is what a UE would have in reality. The simulator does not check that a UE
  could actually decode the PSFCH, decide and prepare within it.

### Measuring the share of timing-feasible reservations

- **What the logs support.** From `_ltx.csv` / `_htx.csv` (`slot_abs`, `harq_id`, `tx_in_tb`, `origin`) and
  `_nack.csv` (NACK times), one can compute realized gaps and the margin under any assumed processing budget P.
  The test is gap ≥ 3 + P. The realized gap distribution is shown above: median 9 slots, 5th percentile 5 slots.
- **What is missing.** The logs record only the slots actually used. Three pieces of instrumentation are needed to
  measure the feasibility of unused reservations, or the margin at decision time:
  - per-grant reserved slot list: `_grants.csv` has only `first_slot` and a slot count;
  - Decide and reclaim timestamps: `decideAtS` is kept in memory but not written;
  - ACK arrival times: only NACKs are logged.
- **Blind helper arm (R4).** Spacing is enforced only by `HelperMinGap` = 2 with a 50-draw fallback to an unchecked
  selection. R4 has no feedback, so only decoding-and-forwarding time matters there.

---

## 3. Cross-transmitter HARQ-IR

1. **How the helper obtains and stores the TB.** It keeps a copy of the decoded MAC PDUs (`Packet::Copy`,
   `NR/nr-sl-ue-mac.cc:1458-1469`) in its own MAC HARQ buffer, through `ForceHarqIdForRelay`. These are decoded
   payload bytes, not coded bits or soft values. The simulator has no coded-bit layer.
2. **What form it stores.** Payload only (see 1).
3. **Its RV.** Fixed to RV 2 for every relay copy (1440; own-resource arm 2219). The source keeps 0, 2, 3, 1
   (`GetRv`). The error model ignores RV.
4. **HARQ ID, NDI and RV on the helper's copy.**
   - The helper signals the source's HARQ id (`relayedHarqId`, 1843).
   - It signals the source's 3GPP NDI bit (`relayedNdiBit`, 1847-1849).
   - It sets the simulator's "new data" flag to 0.
   - The helper's local HARQ id never appears on air.
5. **Source and destination IDs in the SCI.**
   - SCI-2A source ID = the leader's (`relayedSrcL2Id`, 1861); destination = the group.
   - The SCI 1-A tag carries the helper's RNTI (2041-2046). The PHY needs this to match PSCCH to PSSCH.
   - The non-standard field `relayedFromRnti` identifies the HARQ owner.
6. **Scrambling and channel estimation.** Not modelled. 5G-LENA has no scrambling, DMRS or channel estimation.
   Each copy is a separate signal with its own SINR per RB.
7. **How receiver buffers identify the TB.** The key is (`harqRnti`, HARQ id), with `harqRnti = relayedFromRnti`
   when it is set (`NR/nr-spectrum-phy.cc:2864-2867`). The leader ignores relayed copies of its own TB (2870-2877).
8. **How soft information from two UEs is combined.** It is not an LLR combination. `NrEesmIrT1`
   (`NR/nr-eesm-ir.cc:35-66`) maps the history of (SINR per RB, code bits) of all stored copies to one effective
   SINR and one effective code rate. Copies from both transmitters enter the same history.
   - Known stock artefact (TEST_PLAN F35, test C06): the exponential average lets a weak earlier copy pull a strong
     later copy below its own threshold.
   - Example: a 0 dB leader copy combined with a 10 dB helper copy fails, although the helper copy alone decodes.
9. **Buffer resets.**
   - On the "new data" flag.
   - On an NDI-bit change: `SlNdiNewTb`, fix `stale-ndi-fix`.
   - On a failed SCI-2A, if `SlAckPrevDecodedTb` is on.
   - With combining off (ablation).
   - A different transmitter does not reset the buffer, because the key is the HARQ owner, not the sender.
10. **Modified procedures.**

    | Category | Items |
    |---|---|
    | Stock | EESM-IR combining; SCI 1-A matching |
    | MAC extensions | Relay grant; usurped slots; SCI-2A side fields; NDI bit; relay gating |
    | PHY changes | HARQ owner key; ignore own relayed copy; NDI-bit flush; re-ACK of decoded TBs; forced helper miss; test hooks |
    | Idealized | Ideal PSFCH; uncounted SCI-2A side bytes; zero processing; RV-agnostic combining |

**Existing test.** C06 `cross_source_combining` (`PL/tests/workflow/C06_cross_source_combining.test.cc`; result
`PL/tests/results/C06_cross_source_combining.json`, status XFAIL only because of F35). With fixed SINR it shows:
- each copy alone fails;
- leader + helper copies are combined into one buffer of length 2 and decode (R4.2, R4_bsc, R6);
- the relayed SCI-2A carries the leader's HARQ id, source ID, NDI and RV 2.

The test is dated 2026-10-27, before `--sciOnEveryTx`. It should be re-run once on the v3 build. It is a seconds-long
micro test.

---

## 4. Resource accounting and collisions

**What the "4 to 12 %" measures.** `ss_pkt` in `paper_data.py::cost_metrics`
(`PL/harq_helper_study/paper_v2/analysis/paper_data.py:278-368`): the PSSCH subchannel-slots actually transmitted by
the leader and the helper, Σ `rb_len` / 10 over `_ltx` + `_htx` in the KPI window, divided by packets. The PSCCH sits
inside the lowest subchannel and adds nothing.

**What it does not include:**
- **Reservations.** The leader announces all 3 slots in SCI 1-A. Unused slots stay reserved for neighbours.
  - Run counter `res_per_pkt_reserved`: aperiodic 9.38-9.46 for R3 vs 9.42-9.52 for R6; periodic 4.87-5.10 vs
    4.86-4.93.
  - These counters are closed-form estimates (TEST_PLAN F02). On the roundabouts they are below the used count for
    R3, so they are not reliable in absolute terms.
  - A measured alternative, `_reserved_subch_slots` from `_grants.csv`, exists in `paper_data.py:307-324`. It is not
    reported.
  - Either way the reservation footprint is unchanged. The saving is in transmissions only.
- **PSFCH.** Accounting values only: 5G-LENA has no PSFCH resource grid.
  - `psfch_alloc_pkt` is exact: 8 per HARQ-enabled PSSCH for Option 2.
  - `psfch_tx_pkt` for Option 2 is an estimate. Leader ACKs are counted for the whole run and scaled to the window,
    and ACKs on helper copies are not logged.
- **Extra SCI.** The helper sends its own SCI 1-A and SCI-2A in each relay slot, in the same resource. The 46 bytes
  of SCI-2A side information have no air cost in the model. The paper's "3 bits" assumes a compact helper index. The
  code carries a 32-bit RNTI.
- **Coordination.** No message is exchanged for the handover. Helper selection uses member positions sampled at the
  CAM period (`camPeriodMs` = 100). I did not find CAM packets transmitted on the channel, so the cost of position
  messages is not represented. This is not verified further.

**Interference at the helper.**
- In R6 the helper transmits on resources selected from the leader's sensing. The helper's own sensing is not used:
  `sel_helper_* = 0`.
- The channel computes interference at each receiver from the helper's real position, so the different interference
  environment is modelled.
- Overlap rates are similar for both transmitters. The metric is the share of transmissions with an RB-overlapping
  concurrent PSSCH anywhere within 5 km (`PL/platoon_harq_robustness_eval.cc:2520-2570`):

  | Case, 60 vehicles | Leader | Helper |
  |---|---|---|
  | Highway, aperiodic | 0.725 | 0.714 |
  | Highway, periodic | 0.390 | 0.392 |
  | Roundabout W, periodic | 0.573 | 0.554 |

  This is a geometric overlap, not a decode-weighted collision. It also counts the leader-helper double
  transmissions of Q9.
- **Counters available:**
  - `lcoll_*`, `hcoll_*`;
  - `cc_*_l` / `cc_*_h` (causes of collision: fresh dynamic grant, announced after selection, SCI not received,
    half-duplex);
  - `nearest_collider_m`, `n_coll` per transmission.
  - The hidden-node instrumentation and the oracle-IUC arm (`--hiddenNode`, `leaderResIuc`) exist but were off in v3.

**Supported claim.** The cooperative scheme sends fewer PSSCH subchannel-slots and fewer PSFCH transmissions per
packet than Option 2. It does not reduce the resources announced to, and avoided by, neighbouring UEs. It is not
"fully resource-efficient".

---

## 5. Reproducibility

| Parameter | Value (v3) | Where | Manuscript |
|---|---|---|---|
| Carrier, bandwidth | 5.89 GHz, 20 MHz | stack band config; `channelBwMHz` default `PL/...eval.cc:1425` | match |
| Numerology | μ = 1 | `PL/...eval.cc:1424` | match |
| MCS | 14 fixed (FixedMcs, NrSlCoopScheduler `Mcs`) | 1427; 1989-1992 (`cs->SetAttribute("Mcs")`) | match |
| Subchannels | 10 PRB, 5 subchannels | `subchPrbs` 1426; `n_subch` in `_run.csv` | match |
| Pool | all slots SL; sensing window 100 ms; selection window 5 (unused by T1/T2); PSCCH 10 RB | `ST/nr_sl_stack.cc:352-367` | match |
| T1, T2 | 2, 33 slots | `ST/nr_sl_stack.cc:162-163` | match |
| Sensing threshold | -128 dBm | `--slThresRsrp` default | stated in old draft only |
| SPS keep, RRI list | 0.8; 0,10,20,50,100,...,1000 | `PL/run_retx.py:26-37` | not stated |
| `sl-MaxNumPerReserve` | 3 | `PL/...eval.cc:1431` | not stated |
| SCI 1-A on every PSSCH | true (v3) | `study.py:145` | not stated |
| PSFCH | period 1, gap 3, processing 2 | 1430; `ST/nr_sl_stack.cc:394`; `NR/nr-sl-ue-mac.cc:151` | processing gap missing |
| HARQ limit | `--maxNumTx=3` (K = 3) everywhere in ns-3 | `study.py:237` | match; K = 2 / 4 only in the model |
| Platoon | 9 (leader + 8), 30 m gap | `numPlatoon` 1421; traces `*_ivd30_*` | match |
| Helper rule | Kuo, per TB, CAM 100 ms, maxmean | `--helperSelect=kuo` | match |
| Leader traffic | periodic1tb: 100 ms, 190/300 B, SPS RRI 100, PDB 100 ms; aperiodic1tb: ≤ 1200 B, dynamic grant, PDB 50 ms | `PL/...eval.cc:1321-1400`; `run.csv` `leader_grant=dynamic` | match |
| TB size | payload + 37 B (`kLeaderPktOverheadB`); mean grant 742 B, 3.2 subchannels (highway aperiodic, 60 vehicles) | 1340-1343; `_run.csv` | not stated |
| Background | 0/15/30/45/60 cars, 1.67 flows/car, 60 % periodic, SPS | `bgFlowsPerCar`, `bgPeriodicFrac` 1546 | evaluation.tex uses 0 and 60 |
| Channel | TR 37.885 V2V highway / urban, geometric NLOSv (vehicle blockers), SUMO buildings 15 m, shadowing on, 3GPP fast fading (update 10 ms), MaxLossDb 150 | `ST/nr_sl_stack.cc:111-120`; `PL/...eval.cc:1860-1940` | match |
| Error model | NrEesmIrT1 | `ST/nr_sl_stack.cc:192` | match |
| Duration | highway KPI 3 to 52.7 s (≈ 500 TBs per run); roundabout: curve window | `_run.csv` `kpi_start`, `kpi_end`, `pkts` | not stated |
| Seeds | 10 (`--seed = --run = 1..10`), same for every arm | `study.py:235-236` | match |

**Mismatches and gaps:**
- SCI-2A "3 bits / same size" vs. 46 uncounted bytes (sections 1 and 4).
- The processing gap, `sl-MaxNumPerReserve`, SCI-on-every-PSSCH, SPS keep probability and run duration are not
  stated.
- The untracked file `NR/nr-spectrum-phy.cc.rej` is stale. The patch matches the working tree, so it does not affect
  the build, but it should be deleted.
- The env hook `MAGIC_HELPER_ID` (`NR/nr-spectrum-phy.cc:2479-2492`, 3139-3151) forces decoding. `launch_v3.sh`
  unsets it, and test A05 guards it.

---

## 6. Model calibration and validation (analytical repository)

- **Calibration** (`AN/notebooks/link_fit_fs.py`, output `results_04/calibration_v3_fitfs.json`):
  - Data: ns-3 R1 (Blind) per-copy logs at 0 vehicles, all 10 seeds, KPI window
    (`link_fit.ns3_attempts`, glob `R1/*_none_s*_att.csv`, `link_fit.py:70-75`).
  - Targets:
    - per-member copy-1 SINR quantiles;
    - P(decoded | logged SINR, SCI decoded);
    - copy-to-copy SINR spread;
    - conditional failure of copy 2 given copy 1, and of copy 3 given copies 1 and 2.
  - Fitted values: L per route and traffic; (n, K_LOS, K_NLOS) per family from a grid.
  - No PRR is used.
  - **Discrepancy.** The manuscript says the fit uses Blind and Blind + C. `link_fit_fs.py` uses R1 only. The older
    `calibration_v3.py` bisects L and g on the group PRR of R1 and R1_bsc (lines 186-197), but it does not produce
    the fitfs parameters.
- **Validation.**
  - `run_fitfs.py` / `calibration_v3.predict_all`: 40 model seeds vs ns-3 remote_v3.
  - `joint_validation.py` (q̃, R_miss) vs `results_10/ns3_helper_rx.csv`.
  - Helper-rx campaign (`PL/harq_helper_study/helper_rx_campaign/`).
- **Independence.**
  - Validation uses the same ns-3 runs: same seeds, traces, routes and the 0-vehicle condition. It differs in the
    schemes (R2 to R6) and in the helper-study runs.
  - Model seed s uses SUMO trace s, as ns-3 does.
  - Calibration and validation are not seed-independent. Blind is not a prediction, because its links were fitted
    on R1.
  - `compare_calibration.py` tabulates the group PRR of five physics variants, including Cooperative, against ns-3.
    The physics family may therefore have been chosen with the validation PRR in view. The code shows the comparison,
    not the decision.
- **Correlated failures.**
  - Common geometry: positions, buildings, vehicle blockers per copy.
  - Distance-correlated shadowing per link (10-13 m).
  - Independent sub-band fading per link.
  - Stylized interference: per-copy hit probability.
  - Helper-member correlation comes from geometry and shadowing. It is checked directly through q̃ and R_miss
    (111 and 57 configurations).
- **Takeover and fallback recovery.** Simulated event by event in the model's protocol (`sim.run`). The gain slopes
  Δ_TO and Δ_FB are computed from the terms of forced-miss runs (`tab_terms.tex`). They are not fitted.

---

## 7. Existing experiments

| Topic | ns-3 | Model |
|---|---|---|
| Missed SCI (helper) | Yes: `helperMissMode=sci`, p = 0.1-0.7 (helper_rx_campaign, `tab_helper_rx.tex`); natural rate `helper_sci_missed_rate` (≈ 0.10 at 60 vehicles, highway aperiodic) | Yes |
| Feedback (PSFCH) loss | Not modelled | "PSFCH loss 40 %": 82.7 % on W; copies 1.70 → 2.94 (highway) |
| Timing-feasible reservations | Enforced by construction; realized gaps ≥ 5 slots (this audit); no feasibility-under-processing study | No |
| K = 2 vs K = 3 | No (all K = 3) | Yes: K = 2, 3, 4 rows in `tab_sensitivity.tex` |
| Helper rules | Kuo vs map (`out/remote_v3_map`, `remote_map_v2`); fixed helper 1-8 (helper_rx_campaign) | True-SNR oracle (94.8 % on W) |
| Background levels | 0/15/30/45/60 (v3) | Stylized interference |
| Worst-member PRR | `_member.csv` (`prr`, `prr_pdb`); fig_helper_rx | Yes |
| All-member delivery | `tab_ns3stats.tex` | Yes |
| Deadline-constrained reception | `prr_pdb` (PDB 100 / 50 ms); no shorter budget in ns-3 | 10 ms budget row |
| Delay distributions | `_member.csv` mean / p50 / p95 / p99; `_pkt.csv` `delay_ms` | Mean delay |
| Collisions on helper copies | overlap rates, `hcoll_*`, `cc_*_h` | Relay hit 40 % |
| Double source + helper transmission | Present in logs, not reported: table in Q9 | Not modelled |

---

## 8. Deliverable

### A. Confirmed implementation facts

1. Reservations come from one leader selection with K = 3. For HARQ arms, consecutive slots respect the
   PSFCH + processing gap (`ST/nr_sl_coop_scheduler.cc:1976-1999`, 2157-2218).
2. Slots 2 and 3 are handed over by marking `usurpedByHelper` (1697-1712). They are announced in the leader's
   SCI 1-A and signalled to the helper in uncounted SCI-2A fields (`NR/nr-sl-ue-mac.cc:1888-1956`).
3. In a handed slot the leader stays silent (1690-1693, 1744, 1781, 1826-1832).
4. The helper relays the decoded payload with the leader's HARQ id, source ID and NDI bit, at RV 2
   (1395-1517, 1842-1861). It cancels when it overhears 7 ACKs and no NACK (1695-1725, 2846-2873).
5. Option-2 decisions, takeover and fallback are in the scenario controller, not the MAC
   (`PL/nack_only_harq.cc:108-115`, 375-667).
6. Receivers combine the copies of both UEs in one buffer, keyed by the HARQ owner
   (`NR/nr-spectrum-phy.cc:2864-2877`). End-to-end proof: test C06.
7. PSFCH is delivered one slot late (`NR/nr-ue-phy.cc:2125-2136`) and without errors (`NR/nr-spectrum-phy.cc:3345`).
8. Takeover and fallback are timely in the simulated timeline. Measured minimum gaps are 5 slots.
9. Double leader + helper transmissions occur after false fallbacks: 0-2 per cell at 0 vehicles, 13-207 at 60
   (Q9).
10. The cost metric counts transmitted PSSCH subchannel-slots. Reservations are the same as for Option 2.

### B. Assumptions and approximations

- **Handover.** Cross-UE use of a reserved resource and the helper identity are not 3GPP procedures. SCI-2A side
  information carries no air cost.
- **HARQ-IR.** An EESM-IR abstraction: RV-agnostic, no coded bits, no scrambling, DMRS or channel estimation.
  The two RV-2 helper copies count as full IR. Weak copies can hurt (F35).
- **Processing.** Zero MAC/controller processing and zero preparation time. PSFCH latency is a provisional one slot.
  SCI detection is instant.
- **PSFCH.** Ideal: no resource mapping, no errors, no PSFCH collisions. The helper NACK is identified by node id.
- **Option 2 control.** Implemented outside the MAC, with synthetic ACK injection.
- **Helper rule.** Kuo's selection uses positions without modelled CAM transmissions (not verified further).
- **Reservation counters.** `res_*` reservation counters are estimates (F02).

### C. Missing functionality or possible bugs

**Definite:**
1. A false fallback produces simultaneous same-resource transmissions by the source and the helper (Q9, up to 20 %
   of fallbacks). The protocol has no guard against it. One possible guard is: reclaim only if the helper's PSFCH or
   its relay SCI was also absent in the previous occasion. The logs show the leader failed to decode an SCI that was
   sent.
2. The manuscript's calibration description (Blind and Blind + C) does not match `link_fit_fs.py` (R1 only).
3. The manuscript's "3 bits / size of SCI 2-A" does not match the simulated 46 extra bytes (32-bit `helperId`,
   explicit relay slots). The paper describes a design that the simulator does not exactly implement.
4. Option-2 `psfch_tx_pkt` is an estimate: ACKs on helper copies are missing.

**Potential:**
1. `helper_nack_no_slot` is 6-35 % of helper NACKs; for example, 127 of 602 on Roundabout W, aperiodic, 0 vehicles.
   It may be benign (a NACK on the leader's last copy after a takeover or fallback) or a binding issue. Check it with
   `_ltx` `origin` and `tx_in_tb`.
2. `res_per_pkt_reserved` is below the used count for R3 on the roundabouts (estimate, F02).
3. The blind-arm `HelperMinGap` silently falls back to an unchecked selection after 50 draws.
4. The relay gating keeps no time window on overheard ACKs. ACKs from the previous TB on the same HARQ id would
   count. This is unlikely at an RRI of 20 ms or more and was not observed.
5. `RxSlPsfch` (stock) indexes `paramIndexes[i]` for `i < m_slRxSigParamInfo.size()`. This would read out of bounds
   if a PSFCH occasion overlapped other signals. It is not triggered: PSFCH is its own reception.

### D. Reviewer concern matrix

The eight comments were not given verbatim. They are taken to be the audit's topics 1-7, with the delay and
deadline metrics split out.

| # | Concern | Repository proves | Unverified | Needed? | Smallest correction |
|---|---|---|---|---|---|
| 1 | Ownership and state machine | Explicit flags, gating, reclaim, check (A1-A5) | No 3GPP basis for the handover | Text only | State it as a proposed extension; add the table |
| 2 | Timing feasibility | 3+2 gap enforced; measured gaps ≥ 5; decisions before deadline | Real UE processing and PSFCH latency | Optional logging | State the idealization; report the measured gap distribution |
| 3 | Cross-UE HARQ-IR | Single buffer across UEs (C06); IDs and NDI | Real LLR IR, RV, scrambling | Re-run C06 on v3 (seconds) | Describe the EESM-IR abstraction and F35 |
| 4 | Resource accounting and collisions | Transmitted-only saving; equal reservations; similar overlap rates | Decode-weighted collision exposure of the helper | Log analysis only | Say "transmitted"; report the reservation parity |
| 5 | Double transmissions / silent-helper ambiguity | Measured (Q9) | Its PRR effect | Log analysis; a guard would need new code and runs | Report the rate; list it as a limitation |
| 6 | Reproducibility | Full table (section 5) | - | No | Add the missing parameters |
| 7 | Model calibration | R1-only fit, no PRR; joint terms validated | Seed independence; physics choice | Optional: refit on held-out seeds | Correct the text; state the shared seeds |
| 8 | Missing scenarios (K, PSFCH loss, deadline) | Model rows exist; ns-3 K = 3 only | ns-3 K = 2, PSFCH loss | Not essential | Label as model-only |

### E. Manuscript-ready paragraphs

**Grant delegation and ownership.** The source performs one Mode-2 selection per TB and reserves up to K = 3
resources, announced in its SCI 1-A. Its scheduler marks the resources after the first as delegated to the helper.
The source names the helper in its second-stage SCI. In a delegated slot the source does not transmit. The source
regains a slot only through a takeover or a fallback. Delegating a reserved resource to another UE is not a 3GPP
procedure. It is the extension this paper proposes.

**State transitions.** The helper relays a decoded TB in the delegated slots. It cancels them once it has
overheard an ACK from every other member and no NACK. A named helper that fails to decode the TB sends a NACK and
never an ACK. On this NACK the source takes the next delegated slot. When the source's Option-2 decision is not
all-ACK and no helper NACK arrived, the source checks for the helper's SCI in the next delegated slot. If it
decodes none, it takes the following slot. If the source misses an SCI that the helper did send, both transmit in
that slot. In ns-3 this affected up to 20 % of fallbacks with 60 background vehicles.

**Timing.** The PSFCH period is one slot, the PSSCH-to-PSFCH gap three slots and the processing gap two slots
(30 kHz). The selection keeps consecutive reserved slots at least five slots apart. Feedback reaches the MAC one
slot after the PSFCH. The takeover decision is taken before the next reserved slot starts, and the fallback check
four slots before. MAC processing and transmission preparation take zero simulated time.

**Cross-UE HARQ-IR.** The helper stores the decoded payload, not soft bits. It transmits it with the source's HARQ
process ID, source ID and NDI, at RV 2, and with the same TB size, MCS and subchannel count. Receivers store both
transmitters' copies in one HARQ process. The 5G-LENA EESM-IR model maps all stored copies to one effective SINR
and code rate. It does not model RV, scrambling or channel estimation, and it is not LLR combining.

**Resource accounting and limitations.** The cost counts the PSSCH subchannel-slots actually transmitted by the
source and the helper. The reservations announced in SCI 1-A are the same as for Option 2, so the saving does not
free resources for neighbouring UEs. PSFCH is modelled without errors or resource collisions. The second-stage SCI
fields of the simulation carry no air cost.

### F. Priority fixes

1. **Correctness of the protocol.**
   - Report the false-fallback double transmission and add it to the state description.
   - Optionally guard the fallback. This needs new code and a re-run of R6 only.
2. **Validity of the evaluation.**
   - Reword the cost claim as "transmitted" and add the reservation parity, computed from `_grants.csv` with the
     existing `_reserved_subch_slots`.
   - Re-run test C06 on the v3 build.
   - Correct the calibration description: R1 only, shared seeds.
   - Label K, PSFCH loss and short-deadline results as model-only.
3. **Reproducibility.**
   - Add the processing gap, `sl-MaxNumPerReserve`, SCI-on-every-PSSCH, SPS keep probability and run duration.
   - Delete `nr-spectrum-phy.cc.rej`.
   - Log reserved slot lists and decision timestamps if timing must be measured for unused reservations.
4. **Presentation.**
   - Describe the SCI-2A helper field as a proposed format, not as the simulated one.
   - Explain the breakdown of `helper_nack_no_slot`.
