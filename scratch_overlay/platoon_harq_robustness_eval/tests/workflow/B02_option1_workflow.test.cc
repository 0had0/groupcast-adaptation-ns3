// B02_option1_workflow (W: SYN_NEAR, SYN_MARGINAL, SYN_DTX, SYN_MCR; R2, R5_S1_H2): HARQ Option 1 (groupcast NACK-only,
// cast type 11): a receiver NACKs only when its SCI was decoded and the TB is corrupt (and, with the MCR gate, within
// 500 m); the transmitter treats "no NACK" as ACK (DTX = ACK) and retransmits only after a NACK.
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"

#include <map>

using namespace ptest;

int
main()
{
    TestReport T("B02", "option1_workflow", "B");
    T.Summary("Option 1: NACK only if the TB failed (SCI decoded, inside the MCR), nothing otherwise; the leader retransmits "
              "exactly when a NACK arrived (DTX = ACK, an inherent Option-1 property); members never ACK.");
    T.AddRef("TS 38.213 16.3, TS 38.321 5.22.1.3 (Option 1: no NACK = ACK) [V]");
    T.AddRef("TS 38.212 8.4.1.1 cast type 11 = groupcast NACK-only");
    ViolationLog V;

    // ---- SYN_NEAR: lossless ----------------------------------------------------------------------------------------------
    {
        T.AddRunset("SYN_NEAR");
        long runs = 0, tbs = 0, oneTx = 0, dec1 = 0, nackRows = 0, nTxAll = 0, implicit = 0, implicitExp = 0, explicitAck = 0;
        for (const auto& rr : Select(LoadManifest("SYN_NEAR"), "R2"))
        {
            Run r;
            if (!r.Load(rr))
            {
                continue;
            }
            ++runs;
            T.AddNRuns(1);
            nackRows += static_cast<long>(r.nackT.nrows);
            explicitAck += static_cast<long>(r.R("explicit_ack_rx", 0));
            implicit += static_cast<long>(r.R("implicit_ack", 0));
            for (const PTx& x : r.txs)
            {
                implicitExp += (x.side == 0 && x.decision == 1) ? 1 : 0;
                nTxAll += (x.side == 0) ? 1 : 0;
            }
            for (const TbView& tv : WindowTbViews(r))
            {
                ++tbs;
                oneTx += tv.leader.size() == 1;
                dec1 += tv.initial->decision == 1;
                if (tv.leader.size() != 1)
                {
                    V.Add("SYN_NEAR R2 single transmission", rr.tag + " tb " + FmtI(tv.id));
                }
            }
        }
        if (runs == 0)
        {
            T.Skip("SYN_NEAR/R2", "no R2 runs");
        }
        else
        {
            T.Check("SYN_NEAR R2: 0 NACKs in _nack.csv", nackRows == 0, Opt().Exp("0").Obs(FmtI(nackRows)).Tol("0").Sev("H").Fnd("F13"));
            T.Check("SYN_NEAR R2: members never ACK (explicit_ack_rx == 0)", explicitAck == 0, Opt().Exp("0").Obs(FmtI(explicitAck)).Tol("0").Sev("M").Fnd("F13").Ref("TS 38.321 5.22.1.3 [V]"));
            T.Check("SYN_NEAR R2: every TB has 1 transmission with decision 1 (implicit ACK)", tbs > 0 && oneTx == tbs && dec1 == tbs,
                    Opt().Exp(FmtI(tbs) + " TBs").Obs(FmtI(oneTx) + " single-tx, " + FmtI(dec1) + " decision 1").Tol("0").Sev("H"));
            T.Check("SYN_NEAR R2: implicit_ack counter == number of decision-1 transmissions", implicit == implicitExp,
                    Opt().Exp(FmtI(implicitExp)).Obs(FmtI(implicit)).Tol("0").Sev("M"));
        }
    }

    // ---- SYN_MARGINAL: per-event invariants (R2, R5H2) ----------------------------------------------------------------
    for (const std::string arm : {"R2", "R5_S1_H2"})
    {
        T.AddRunset("SYN_MARGINAL");
        long txs = 0, aMiss = 0, aOk = 0, bExtra = 0, bOk = 0, cBad = 0, cNackNoCorrupt = 0, cNackBeyond = 0, cNackSci0 = 0,
             dBad = 0, dN = 0, corruptNoNack = 0, corruptTotal = 0, nackUnmatched = 0, nackTotal = 0, nackAmb = 0, runs = 0,
             sentVsRecv = 0;
        for (const auto& rr : Select(LoadManifest("SYN_MARGINAL"), arm))
        {
            Run r;
            if (!r.Load(rr) || !r.hasAtt)
            {
                continue;
            }
            ++runs;
            T.AddNRuns(1);
            nackUnmatched += r.nackUnmatched;
            nackAmb += r.nackAmbiguous;
            nackTotal += static_cast<long>(r.nacks.size());
            for (const TbView& tv : WindowTbViews(r))
            {
                const bool leaderOnly = (arm == "R2");
                for (size_t k = 0; k < tv.all.size(); ++k)
                {
                    const PTx& x = *tv.all[k];
                    ++txs;
                    if (x.nNack != x.nNackSent)
                    {
                        ++sentVsRecv;
                    }
                    // (c) NACK senders vs decode status
                    for (int m = 1; m <= kMembers; ++m)
                    {
                        const bool nacked = (x.nackMask >> (m - 1)) & 1;
                        const bool self = r.IsSelf(x, m);
                        const bool decodedBefore = (k > 0) && r.DecodedBy(*tv.tb, static_cast<int>(k) - 1, m);
                        if (nacked)
                        {
                            if (x.status[m - 1] == 1)
                            {
                                ++cNackNoCorrupt;
                                V.Add(arm + " (c) member with status 1 sent a NACK", rr.tag + " tx slot " + FmtI(x.slot) + " member " + std::to_string(m));
                            }
                            else if (x.status[m - 1] == 0)
                            {
                                ++cNackSci0;
                                V.Add(arm + " (c) member without SCI sent a NACK", rr.tag + " tx slot " + FmtI(x.slot) + " member " + std::to_string(m));
                            }
                            else if (x.dist[m - 1] > 500.0)
                            {
                                ++cNackBeyond;
                                V.Add(arm + " (c) NACK beyond the MCR", rr.tag + " tx slot " + FmtI(x.slot) + " member " + std::to_string(m) + " dist " + Fmt(x.dist[m - 1], 5));
                            }
                        }
                        else if (x.status[m - 1] == 2 && !self && !decodedBefore)
                        {
                            ++corruptTotal;
                            if (x.dist[m - 1] <= 500.0)
                            {
                                ++corruptNoNack; // SCI-2A corrupt (no feedback possible) is the only legitimate reason
                            }
                        }
                        if (nacked)
                        {
                            ++corruptTotal;
                        }
                    }
                    // (d) decision 0 <=> n_nack > 0
                    if (x.side == 0 && (x.decision == 0 || x.decision == 1))
                    {
                        ++dN;
                        const bool ok = (x.decision == 0) == (x.nNack > 0);
                        dBad += ok ? 0 : 1;
                        if (!ok) V.Add(arm + " (d) decision 0 <=> n_nack > 0", rr.tag + " tx slot " + FmtI(x.slot));
                    }
                    // (a)/(b) retransmission only after a NACK; only leader-only arm (R2): the next transmission of the TB
                    if (leaderOnly && k + 1 <= tv.all.size())
                    {
                        const bool hasNext = k + 1 < tv.all.size();
                        const bool last = static_cast<int>(k) + 1 >= r.maxNumTx;
                        if (x.nNack > 0 && !last)
                        {
                            if (hasNext)
                            {
                                ++aOk;
                            }
                            else
                            {
                                ++aMiss;
                                V.Add("R2 (a) NACK without retransmission", rr.tag + " tx slot " + FmtI(x.slot) + " decision " + std::to_string(x.decision));
                            }
                        }
                        if (x.nNack == 0 && x.decision == 1)
                        {
                            if (hasNext)
                            {
                                ++bExtra;
                                V.Add("R2 (b) retransmission without NACK", rr.tag + " tx slot " + FmtI(x.slot));
                            }
                            else
                            {
                                ++bOk;
                            }
                        }
                    }
                }
            }
        }
        if (runs == 0)
        {
            T.Skip("SYN_MARGINAL/" + arm, "no runs");
            continue;
        }
        const std::string pre = "SYN_MARGINAL " + arm + ": ";
        T.Check(pre + "every _nack.csv row maps to a transmission (same RNTI and HARQ id, decoded inside its slot; ambiguity is B04)", nackUnmatched == 0,
                Opt().Exp("0 unmatched").Obs(FmtI(nackUnmatched) + " unmatched of " + FmtI(nackTotal) + " (" + FmtI(nackAmb) + " have a second TB of the same HARQ id within 32 slots)").Tol("0").Sev("M"));
        T.Check(pre + "NACKs sent by members == NACKs received by the leader per transmission (lossless PSFCH, DV1)", sentVsRecv == 0,
                Opt().Exp("0 differences").Obs(FmtI(sentVsRecv) + " of " + FmtI(txs) + " transmissions").Tol("0").Sev("L").Fnd("DV1").Note("genuine, rare (R5 only, 2 of 14440 transmissions in the pilot): in both events two members NACK the same transmission in the same PSFCH occasion (members 5+6, members 6+8) and the leader credits one of them (n_nack 1, n_nack_sent 2); no half-duplex (the leader does not transmit in the following 8 slots).  Not seen with R2 (shared resource, 7000+ multi-NACK transmissions exact) so it is specific to the R5 helper-NACK path (--opt1HelperNack: the helper's NACK goes to the dedicated resource); cause not isolated further, to be reported to the simulator owner"));
        T.Check(pre + "(c) a member with a decoded TB (status 1) never NACKs", cNackNoCorrupt == 0,
                Opt().Exp("0").Obs(FmtI(cNackNoCorrupt)).Tol("0").Sev("H").Fnd("F13").Ref("TS 38.213 16.3 [V]"));
        T.Check(pre + "(c) a member that missed the SCI (status 0) never sends feedback", cNackSci0 == 0,
                Opt().Exp("0").Obs(FmtI(cNackSci0)).Tol("0").Sev("H").Ref("TS 38.213 16.3 (no feedback without SCI-2) [V]"));
        T.Check(pre + "(c) no NACK from a receiver beyond the MCR (500 m)", cNackBeyond == 0,
                Opt().Exp("0").Obs(FmtI(cNackBeyond)).Tol("0").Sev("M").Fnd("F13").Ref("TS 38.213 16.3, SCI 2-B distance rule [V]"));
        T.Info(pre + "corrupt member-transmissions inside the MCR with no NACK (SCI-2A corrupt, no feedback possible)",
               Opt().Obs(FmtI(corruptNoNack) + " of " + FmtI(corruptTotal) + " corrupt member-transmissions"));
        T.Check(pre + "(d) decision 0 <=> n_nack > 0", dBad == 0, Opt().Exp("0 violations of " + FmtI(dN)).Obs(FmtI(dBad)).Tol("0").Sev("H"));
        if (arm == "R2")
        {
            T.Check(pre + "(a) a NACK on transmission k < maxNumTx is followed by transmission k+1", aMiss == 0,
                    Opt().Exp("0 missed of " + FmtI(aOk + aMiss)).Obs(FmtI(aMiss)).Tol("0").Sev("H").Ref("TS 38.321 5.22.1.3 [V]"));
            T.Check(pre + "(b) no transmission k+1 without a NACK on k (no retransmission after the implicit ACK)", bExtra == 0,
                    Opt().Exp("0 of " + FmtI(bOk + bExtra)).Obs(FmtI(bExtra)).Tol("0").Sev("H").Ref("TS 38.321 5.22.1.3 [V]"));
        }
        else
        {
            T.Info(pre + "(a)/(b) the retransmitter is the helper: relay gating is checked in C02", Opt());
        }
    }

    // ---- SYN_DTX ------------------------------------------------------------------------------------------------------------
    {
        T.AddRunset("SYN_DTX");
        long runs = 0, m8Att = 0, m8Status0 = 0, m8Nacks = 0, tbs1 = 0, tbs1One = 0;
        std::vector<double> m8prr;
        for (const auto& rr : Select(LoadManifest("SYN_DTX"), "R2"))
        {
            Run r;
            if (!r.Load(rr) || !r.hasAtt)
            {
                continue;
            }
            ++runs;
            T.AddNRuns(1);
            for (const auto& n : r.nacks)
            {
                m8Nacks += (n.member == 8);
            }
            m8prr.push_back(r.MemberCol("prr", 8));
            for (const TbView& tv : WindowTbViews(r))
            {
                for (const PTx* x : tv.all)
                {
                    ++m8Att;
                    m8Status0 += (x->status[7] == 0);
                }
                bool all7 = true;
                for (int m = 1; m <= 7; ++m) all7 = all7 && tv.initial->status[m - 1] == 1;
                if (all7)
                {
                    ++tbs1;
                    tbs1One += (tv.leader.size() == 1);
                }
            }
        }
        if (runs == 0)
        {
            T.Skip("SYN_DTX/R2", "no runs");
        }
        else
        {
            double mx = 0;
            for (double p : m8prr) mx = std::max(mx, p);
            T.Check("SYN_DTX R2: member 8 (3000 m, MaxLossDb 110) never decodes an SCI (status 0 on every attempt)", m8Att > 0 && m8Status0 == m8Att,
                    Opt().Exp("status 0 on all " + FmtI(m8Att)).Obs(FmtI(m8Status0)).Tol("0").Sev("H"));
            T.Check("SYN_DTX R2: member 8 never NACKs", m8Nacks == 0, Opt().Exp("0").Obs(FmtI(m8Nacks)).Tol("0").Sev("H"));
            T.Check("SYN_DTX R2: member 8 PRR ~ 0", mx < 0.01, Opt().Exp("< 0.01").Obs("max over runs " + Fmt(mx, 4)).Sev("M"));
            T.Check("SYN_DTX R2: TBs end after 1 transmission whenever members 1..7 decode the first", tbs1 > 0 && tbs1One == tbs1,
                    Opt().Exp(FmtI(tbs1)).Obs(FmtI(tbs1One)).Tol("0").Sev("H"));
            T.Info("SYN_DTX R2: DTX = ACK is an inherent Option-1 property, not a bug",
                   Opt().Note("a receiver that cannot decode the SCI sends nothing, so the leader reads silence as success and member 8 "
                              "is never served (3GPP Option 1 semantics reproduced)").Ref("TS 38.321 5.22.1.3 [V]"));
        }
    }

    // ---- SYN_MCR ---------------------------------------------------------------------------------------------------------------
    {
        T.AddRunset("SYN_MCR");
        long runs = 0, st2 = 0, nacks8 = 0, nacks8R3 = 0, st2R3 = 0, tbsOnly8 = 0, tbsOnly8One = 0;
        for (const char* arm : {"R2", "R3"})
        {
            for (const auto& rr : Select(LoadManifest("SYN_MCR"), arm))
            {
                Run r;
                if (!r.Load(rr) || !r.hasAtt)
                {
                    continue;
                }
                ++runs;
                T.AddNRuns(1);
                const bool isR2 = (std::string(arm) == "R2");
                for (const auto& n : r.nacks)
                {
                    (isR2 ? nacks8 : nacks8R3) += (n.member == 8);
                }
                for (const TbView& tv : WindowTbViews(r))
                {
                    for (const PTx* x : tv.all)
                    {
                        (isR2 ? st2 : st2R3) += (x->status[7] == 2);
                    }
                    if (isR2)
                    {
                        bool others = true;
                        for (int m = 1; m <= 7; ++m) others = others && tv.initial->status[m - 1] == 1;
                        if (others && tv.initial->status[7] == 2)
                        {
                            ++tbsOnly8;
                            tbsOnly8One += (tv.leader.size() == 1);
                        }
                    }
                }
            }
        }
        if (runs == 0)
        {
            T.Skip("SYN_MCR", "no runs");
        }
        else if (st2 == 0)
        {
            T.Skip("SYN_MCR R2: member 8 (650 m) corrupt but silent", "member 8 never reached status 2 (SCI always missed): the gate is not exercised");
        }
        else
        {
            T.Check("SYN_MCR R2: member 8 (650 m > MCR 500 m) sends 0 NACKs although its TB is corrupt", nacks8 == 0,
                    Opt().Exp("0 NACKs for " + FmtI(st2) + " corrupt attempts").Obs(FmtI(nacks8)).Tol("0").Sev("M").Fnd("F13")
                        .Ref("TS 38.213 16.3 / SCI 2-B communication range [V]"));
            T.Check("SYN_MCR R2: a TB that only member 8 failed triggers no retransmission", tbsOnly8 == 0 || tbsOnly8One == tbsOnly8,
                    Opt().Exp("single transmission").Obs(FmtI(tbsOnly8One) + " of " + FmtI(tbsOnly8)).Tol("0").Sev("M"));
            T.Check("SYN_MCR R3 (Option 2 control): member 8 does send NACKs (no range gate)", st2R3 > 0 && nacks8R3 > 0,
                    Opt().Exp("> 0 NACKs for " + FmtI(st2R3) + " corrupt attempts").Obs(FmtI(nacks8R3)).Sev("M")
                        .Ref("TS 38.321 5.22.1.3 (Option 2 uses zone-ID, not a distance gate here) [V]"));
        }
    }
    const std::string art = V.Write("B02_violations");
    if (!art.empty())
    {
        T.Artefact(art);
    }
    return T.Finish();
}
