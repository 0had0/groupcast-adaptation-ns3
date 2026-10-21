#include "common/analysis_kit.h"

#include <cmath>
#include <unordered_map>

namespace ptest
{

const char*
EffName(Eff e)
{
    switch (e)
    {
    case Eff::Self: return "self";
    case Eff::Effective: return "effective";
    case Eff::Redundant: return "redundant";
    case Eff::FutileSci: return "futile_sci_missed";
    case Eff::FutileColl: return "futile_corrupt_collision";
    case Eff::FutileChannel: return "futile_corrupt_channel";
    }
    return "?";
}

const char*
CatName(Cat c)
{
    switch (c)
    {
    case Cat::Delivered: return "delivered";
    case Cat::NoTx: return "no_tx";
    case Cat::StopSciMissed: return "T3_stop_sci_missed";
    case Cat::StopSci2NoNack: return "T3c_corrupt_no_nack";
    case Cat::StopHelperBlindSpot: return "G08_helper_blind_spot";
    case Cat::StopHelperNotDecoded: return "stop_helper_not_decoded";
    case Cat::StopStale: return "T6_stop_stale";
    case Cat::StopMcr: return "T4_stop_mcr";
    case Cat::StopPdb: return "T2_stop_pdb";
    case Cat::StopOther: return "stop_other";
    case Cat::Exhausted: return "exhausted_channel_collision";
    default: return "?";
    }
}

Eff
ClassifyAttempt(const Run& r, const TbView& tv, size_t k, int m)
{
    const PTx& x = *tv.all[k];
    if (r.IsSelf(x, m))
    {
        return Eff::Self;
    }
    if (k > 0 && r.DecodedBy(*tv.tb, static_cast<int>(k) - 1, m))
    {
        return Eff::Redundant;
    }
    const int st = x.status[m - 1];
    if (st == 1)
    {
        return Eff::Effective;
    }
    if (st == 0)
    {
        return Eff::FutileSci;
    }
    return x.nColl > 0 ? Eff::FutileColl : Eff::FutileChannel;
}

int
WorstMemberByPrr(const Run& r)
{
    int w = kMembers;
    double best = 2.0;
    for (int m = kMembers; m >= 1; --m)
    {
        const double p = r.MemberCol("prr_pdb", m);
        if (!std::isnan(p) && p < best - 1e-12)
        {
            best = p;
            w = m;
        }
    }
    return w;
}

void
ClassifyPairs(const Run& r, std::vector<PairOutcome>& out)
{
    const std::string retx = r.ref.Retx();
    const bool helperArm = retx == "R4" || retx == "R5" || retx == "R6";
    const bool opt1 = retx == "R2" || retx == "R5";
    // delivered lookup: member -> seq -> rx
    std::vector<std::unordered_map<int64_t, bool>> rxOf(kMembers);
    for (int m = 0; m < kMembers; ++m)
    {
        for (const auto& p : r.pkts[m])
        {
            rxOf[m][p.seq] = p.rx;
        }
    }
    const double stepS = 5 * kSlotS; // K + MinTimeGapProcessing slots: earliest next retransmission after a transmission
    for (const auto& [id, tb] : r.tbs)
    {
        if (std::isnan(tb.tGen) || tb.firstSeq < 0)
        {
            continue;
        }
        // build the view (window membership by generation time)
        TbView tv;
        tv.id = id;
        tv.tb = &tb;
        tv.tGen = tb.tGen;
        for (int u : tb.tx)
        {
            const PTx* x = &r.txs[u];
            tv.all.push_back(x);
            (x->side == 0 ? tv.leader : tv.helper).push_back(x);
            if (x->side == 0 && x->txInTb == 1 && !tv.initial)
            {
                tv.initial = x;
            }
        }
        if (!tv.initial)
        {
            continue;
        }
        if (helperArm)
        {
            tv.helperNode = r.HelperOf(*tv.initial);
        }
        const int nTx = static_cast<int>(tv.all.size());
        const PTx& last = *tv.all.back();
        bool anyStale = false;
        for (const PTx* x : tv.leader)
        {
            anyStale = anyStale || x->decision == 2;
        }
        for (int m = 1; m <= kMembers; ++m)
        {
            auto it = rxOf[m - 1].find(tb.firstSeq);
            if (it == rxOf[m - 1].end())
            {
                continue;
            }
            PairOutcome po{id, m, Cat::Delivered, nTx};
            po.cond = tv.initial->cond[m - 1];
            po.dist = tv.initial->dist[m - 1];
            po.sinr = (tv.initial->status[m - 1] == 0) ? NAN : tv.initial->sinr[m - 1];
            po.coll = tv.initial->nColl > 0;
            po.status0 = tv.initial->status[m - 1];
            {
                bool dec = false;
                for (size_t k = 0; k < tv.all.size(); ++k)
                {
                    if (r.IsSelf(*tv.all[k], m)) continue;
                    const int st = tv.all[k]->status[m - 1];
                    if (st == 1 && !dec)
                    {
                        dec = true;
                        po.deliverIdx = static_cast<int>(k);
                        po.tDeliverOffsetMs = (tv.all[k]->t - tv.initial->t) * 1e3;
                    }
                    if (st == 0 && !dec) po.everSciMissed = true;
                }
            }
            if (it->second)
            {
                out.push_back(po);
                continue;
            }
            Cat c = Cat::StopOther;
            int lastStatus = -1;
            int lastK = -1;
            bool decodedPhy = false;
            for (size_t k = 0; k < tv.all.size(); ++k)
            {
                if (r.IsSelf(*tv.all[k], m))
                {
                    continue;
                }
                const int st = tv.all[k]->status[m - 1];
                if (st == 1)
                {
                    decodedPhy = true;
                }
                if (st != 0 || !decodedPhy)
                {
                    lastStatus = st;
                    lastK = static_cast<int>(k);
                }
            }
            if (nTx == 0)
            {
                c = Cat::NoTx;
            }
            else if (nTx >= r.maxNumTx)
            {
                c = Cat::Exhausted;
            }
            else
            {
                const bool helperDecoded =
                    helperArm && tv.helperNode >= 1 && tv.helperNode <= kMembers && r.DecodedBy(*tv.tb, nTx - 1, tv.helperNode);
                if (retx == "R6" && m == tv.helperNode && tv.initial->status[m - 1] == 0 && tv.leader.back()->decision == 1)
                {
                    c = Cat::StopHelperBlindSpot;
                }
                else if (helperArm && !helperDecoded && tv.helper.empty())
                {
                    c = Cat::StopHelperNotDecoded;
                }
                else if (anyStale)
                {
                    c = Cat::StopStale;
                }
                else if (opt1 && lastStatus == 0)
                {
                    c = Cat::StopSciMissed;
                }
                else if (opt1 && lastStatus == 2 && tv.initial->dist[m - 1] > 500.0)
                {
                    c = Cat::StopMcr;
                }
                else if (opt1 && lastStatus == 2 && lastK >= 0 && ((tv.all[lastK]->nackMask >> (m - 1)) & 1u) == 0)
                {
                    c = Cat::StopSci2NoNack;
                }
                else if (last.t + stepS > tv.tGen + r.pdbS)
                {
                    c = Cat::StopPdb;
                }
            }
            po.cat = c;
            out.push_back(po);
        }
    }
}

} // namespace ptest
