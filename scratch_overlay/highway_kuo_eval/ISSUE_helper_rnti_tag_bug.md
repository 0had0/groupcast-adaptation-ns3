# ISSUE: Cooperative helper retransmissions carry the Leader's RNTI tag, so receivers drop them

> **Resolved 2026-10-11** (laptop `contrib/nr` working tree, not committed; previous state saved in
> `nr_patch_backup/nr_coop_before_2026-10-11.patch`). The scheme was reworked rather than patched:
>
> * **Side information in SCI-2A** (`NrSlSciF2aHeader::CoopInfo`): the leader names the helper and
>   lists the reserved slots / subchannels it hands over (absolute `SfnSf`), plus MCS, TB size and
>   PSCCH/PSSCH layout. The helper relays the decoded TB **unchanged** in exactly those slots (was:
>   gaps from the *last* sensed SCI-1A, `+1 slot` offset, MCS 14 hard-coded, 2nd gap off by one).
>   The error model still uses the real 5-byte SCI-2A (`GetAirSize()`; was 15 B for every run).
> * **Soft combining**: the helper's SCI-2A carries `relayedFromRnti`, the leader's HARQ id and source
>   L2 id; receivers combine / de-duplicate it in the **leader's** HARQ process (was: history copied
>   to the helper's RNTI *before* the leader's attempt was added, under the leader's HARQ id while the
>   helper signalled its own one; `MAGIC_ALIAS_HARQ` removed). Verified: failed helper copies are
>   decoded with 2 history entries.
> * **Leader**: gives up exactly the slots its scheduler marks (`usurpedByHelper`, from
>   `LeaderTxCount`) — the `RNTI == 1` override that suppressed *all* leader retransmissions is gone —
>   and emits no PSCCH/PSSCH/trace for them. One relay grant / HARQ process per TB with one transmission
>   per handed-over slot (the old code could send only one helper copy).
> * **Coop scheduler** `HelperMinGap` (default 2 slots): with blind reservations the next slot was often
>   the helper's, too early to decode-and-forward (half the relays were impossible); selection now
>   redraws until the helper's first slot is >= 2 slots after the leader's last one.
> * Changes that leaked into every simulation are removed or opt-in: "wait for 8 ACKs" now only with
>   `NUM_MEMBERS` set; per-slot `NS_LOG_UNCOND` removed. `highway_kuo_eval.cc`: dead
>   `DecodeLatencyMode` line removed (did not compile).
>
> Result (IVD 30 m, static platoon, periodic2, helper = node 4, 5 seeds x 5 s, no genie), tail member
> PRR: Leader 2 tx 0.57 -> Leader 1 + helper 1 **0.70**; Leader 3 tx 0.58 -> Leader 2 + helper 1
> **0.67**, Leader 1 + helper 2 **0.69**. The original analysis below is kept for reference.

**Status:** Root cause found. Workaround patch is in place (PHY-level tag rewrite). Not yet validated on PRR/KPIs. Debug logging is still in the code.
**Date:** 2026-10-07
**Affected scenarios:** `Coop_1TX_1H` and `Coop_2TX_1H` (chained reservations with the helper doing the retransmission)
**Unaffected scenarios:** `Leader_2TX` and `Leader_3TX`
**Files involved:**
- `contrib/nr/model/nr-sl-ue-mac.cc` (`DoReceivePsschPhyPdu`, `DoNrSlSlotIndication`, `DoTransmitNrSlRlcPdu`)
- `contrib/nr/model/nr-sl-ue-mac-harq.cc` (`ForceHarqIdForRelay`, `GetPacketBurst`, `FlushHarqBuffer`)
- `contrib/nr/model/nr-spectrum-phy.cc` (`StartTxSlDataFrames`, `RxSlPssch`)
- `contrib/nr/model/nr-harq-phy.cc` (`AliasSlDataHarqProcess`, the receiver soft-buffer aliasing)

---

## 1. Symptom

With a 0 % helper drop rate (the helper always decodes the Leader's first TX) and the helper chosen by Kuo's method, the cooperative scheme did **worse** than the Leader-only baseline at IVD = 30 m:

| Scenario | Worst-member PRR (Node 8) |
|---|---|
| Leader_2TX (1 new TX + 1 Leader reTX) | **46.94 %** |
| Coop_1TX_1H (1 Leader TX + 1 helper reTX) | **36.73 %** |

The helper is closer to the far members than the Leader is. Even with no combining, its retransmission should at least match the Leader's reTX. With HARQ-IR soft combining it should do clearly better. So a PRR below baseline points to a functional bug, not a radio effect.

## 2. Background: how NR-SL PHY links a PSSCH to its SCI

In `NrSpectrumPhy::RxSlPssch`, each received PSSCH transport block is matched to the SCI-1A decoded earlier in the same slot. The match uses the `LteRadioBearerTag` on the **first packet** of the `PacketBurst`:

```cpp
pb->GetPackets().front()->PeekPacketTag(tag);   // tag.GetRnti() == txRnti expected
```

If the tag's RNTI is not the RNTI of the UE that sent the SCI, the TB is never matched. It is then either discarded or counted against the wrong HARQ process. The soft-buffer combining added in request #10 (`AliasSlDataHarqProcess`, `MAGIC_ALIAS_HARQ`) also keys on `(txRnti, harqId)`, so it depends on the same field.

The tag is first set by the transmitting MAC in `DoTransmitNrSlRlcPdu`:

```cpp
LteRadioBearerTag bearerTag(params.rnti, params.lcid, 0);   // RNTI of the ORIGINAL sender
params.pdu->AddPacketTag(bearerTag);
```

## 3. What was observed (trace evidence)

Temporary `NS_LOG_UNCOND` traces gave the following:

1. **The MAC transmits the helper's grant.** Node 5 (RNTI 5, the helper) prints `intercepted Helper slot!` for every Leader TB.
2. **The PHY sends it.** Node index 4 (the helper device, RNTI 5) calls `StartTxSlDataFrames` on the expected slots.
3. **Receivers decode the helper's SCI-1A.** Node 8 decoded the helper's SCI-1A 52 times.
4. **The PSSCH payload carries the wrong RNTI.** Before the fix, every helper burst logged:
   ```
   Node 4 PHY StartTxSlDataFrames transmitting burst with tag RNTI: 1
   ```
   The SCI says `txRnti = 5`, but the data says `RNTI = 1`. `RxSlPssch` on Node 8 therefore extracted `RNTI: 1` and could not match the TB to the helper's SCI. The helper's retransmission was effectively lost.

Net result: in `Coop_1TX_1H`, far members only ever had the Leader's single TX. That is why the cooperative scheme scored below `Leader_2TX`, which at least delivers a second, matchable copy.

## 4. Root cause

There were two mistakes.

### 4a. The tag rewrite was in the wrong execution context

The first fix tried to replace the `LteRadioBearerTag` inside `NrSlUeMac::DoNrSlSlotIndication`, under `if (m_enableChainedRes && isUsurped)`. That function runs on the **transmitting node that owns the grant**. For this branch, that is the **Leader** (`GetRnti() == 1`). The code therefore wrote `1` over a tag that was already `1`, which did nothing.

The helper actually picks up the TB in `NrSlUeMac::DoReceivePsschPhyPdu` (receive context, when `sciF2a.GetHelperId() == GetRnti()`). It then loads the TB into its own HARQ buffer through `NrSlUeMacHarq::ForceHarqIdForRelay(harqId, dst, 2, pdu)`. That `pdu` is the burst **as received**, so it still carries the Leader's tag (RNTI 1).

### 4b. The relay buffer is overwritten by an untagged-fixed copy

In `DoReceivePsschPhyPdu` there are **three** calls to `ForceHarqIdForRelay`:

| Line (approx.) | Argument | Tag RNTI |
|---|---|---|
| 974 | `pdu` (raw received burst) | **1 (Leader)** |
| 1032 | `relayPb` (copies with tag rewritten to `GetRnti()`) | 5 |
| 1050 | `relayPb` (second-gap path, scenario 3) | depends on path |

After that, `m_nrSlHarq->AddPacket(..., pktIt->Copy())` appends the **original** `dataPkts` (tag = 1) to the buffer. A later relay path or `GetPacketBurst` can also hand the PHY a burst whose `front()` packet still carries RNTI 1. So even after the rewrite moved into the helper's receive path (the log showed `Replaced tag for Helper! ... expected=5`), the PHY still transmitted `RNTI: 1`. The rewritten `relayPb` is not reliably the burst that ends up being sent.

A standalone check (`test_tag2.cc`) confirmed that `Packet::ReplacePacketTag` works and that `PacketBurst::Copy()` keeps the new tag:

```
Found tag with RNTI: 1
After replace, RNTI: 5
After pb copy, RNTI: 5
```

So the problem is not in ns-3's tag mechanics. It is about **which** burst object reaches the PHY.

## 5. Current workaround (in place)

In `NrSpectrumPhy::StartTxSlDataFrames`, every outgoing PSSCH packet's `LteRadioBearerTag` RNTI is now forced to the transmitting UE's own RNTI:

```cpp
Ptr<NrUePhy> uePhy = DynamicCast<NrUePhy>(m_phy);
if (uePhy) {
    uint16_t myRnti = uePhy->GetRnti();
    for (auto p : pb->GetPackets()) {
        LteRadioBearerTag tag;
        if (p->PeekPacketTag(tag) && tag.GetRnti() != myRnti) {
            tag.SetRnti(myRnti);
            p->ReplacePacketTag(tag);
            NS_LOG_UNCOND("PHY FORCED RNTI to " << myRnti << " for packet!");
        }
    }
}
```

Reasoning: the RNTI in the PHY tag must always identify the **physical transmitter**, because the SCI-1A that pairs with it comes from that transmitter. Fixing it as the last step before the air interface makes this true no matter which MAC/HARQ path built the burst. The sim-level tie back to the *original* packet (Leader / HARQ process) is handled on the receiver side by the soft-buffer aliasing from request #10 (SCI-2 helperId, then `AliasSlDataHarqProcess`, plus the `MAGIC_ALIAS_HARQ` fallback). The data does not need to impersonate the Leader.

**Verified after the patch (IVD 30 m, Coop_1TX_1H):**
```
PHY FORCED RNTI to 5 for packet!
Node 4 PHY StartTxSlDataFrames transmitting burst with tag RNTI: 5
```

**Not yet verified:** that Node 8's PRR goes up, and that `psschRxUePhy` now logs `txRnti = 5` matched TBs. The KPI query failed because the DB is `scratch/highway_kuo_eval/results/ivd_30_<scenario>.db`, not `.../ivd_30_magic/prr_type1.db`.

## 6. Side effects / risks of the workaround

- **Normal (non-relay) TX:** no change, because the tag already equals the transmitter's RNTI.
- **RLC/PDCP reassembly at receivers:** for relayed packets, the receiver MAC now sees `rnti = helper`. If upper layers key reassembly on the tag RNTI rather than `srcL2Id` in the SL-SCH subheader, relayed packets could go to the wrong RLC entity. The SL path in 5G-LENA uses `srcL2Id/dstL2Id/lcid`, so this should be fine, but it **must be checked against the PRR counters**, which use the application-level source.
- **Soft combining:** this only works if the receiver aliasing maps `(helperRnti, harqId)` to `(leaderRnti, harqId)`. If the SCI-2 is missed and `MAGIC_ALIAS_HARQ=0`, the helper's copy is decoded on its own (no IR gain). That is the intended, logged behaviour.

## 7. Clean-up TODO (before production runs)

1. Remove the first `ForceHarqIdForRelay(harqId, ..., pdu)` at ~line 974, or pass the rewritten burst there. Make sure `AddPacket` gets tag-rewritten copies, so the MAC buffer is correct without relying on the PHY workaround.
2. Remove the debug prints:
   - `nr-sl-ue-mac.cc`: `Replaced tag for Helper!`, `Added packet to relayPb with RNTI`, `MAC SendPsschMacPdu tag ...` (inserted before both `SendPsschMacPdu(itPkt, ...)` calls), `intercepted Helper slot!`
   - `nr-spectrum-phy.cc`: `PHY StartTxSlDataFrames transmitting burst with tag RNTI`, `PHY FORCED RNTI ...` (keep the rewrite, drop the log)
   - Any `COMPARING NUMEROLOGY` print.
3. Note: `nr-sl-ue-mac-harq.cc` was briefly broken by a scripted debug insert, then repaired by hand (two stray `}` removed in `FlushHarqBuffer`, ~line 298). `contrib/nr` is not tracked by the outer git repo, so diff it against upstream 5G-LENA to confirm nothing else changed.
4. Re-run `python3 run_ivd_sweep.py` (build with `./ns3 build -j 16`) and confirm the expected ordering at IVD 30 m:
   `Coop_2TX_1H ≥ Leader_3TX` and `Coop_1TX_1H ≥ Leader_2TX` for the worst member.
5. Then run the full IVD 10–60 m sweep, re-plot worst-member PRR vs distance for the 4 scenarios, and do the Kuo sensitivity analysis.

## 8. Lesson

In ns-3 NR sidelink, **the node that owns a callback is not always the node you think**. `DoNrSlSlotIndication` runs on the grant owner (TX side). `DoReceivePsschPhyPdu` runs on every receiver, including the helper. Any change to "what the helper sends" has to happen in the helper's receive path, or, more robustly, at the PHY TX boundary, where the transmitter's identity is known for certain.
