#include "common/run_outputs.h"

#include <algorithm>
#include <cmath>
#include <iostream>
#include <numeric>
#include <set>

namespace ptest
{

bool
Run::Load(const RunRef& r, std::string* err)
{
    ref = r;
    if (!LoadTable(r.prefix + "_run.csv", runT) || runT.nrows == 0)
    {
        if (err)
        {
            *err = "missing " + r.prefix + "_run.csv";
        }
        return false;
    }
    LoadTable(r.prefix + "_member.csv", memberT);
    LoadTable(r.prefix + "_pkt.csv", pktT);
    hasLtx = LoadTable(r.prefix + "_ltx.csv", ltxT);
    hasHtx = LoadTable(r.prefix + "_htx.csv", htxT);
    LoadTable(r.prefix + "_nack.csv", nackT);
    hasAtt = LoadTable(r.prefix + "_att.csv", attT);
    hasAlltx = LoadTable(r.prefix + "_alltx.csv", alltxT);
    hasGrants = LoadTable(r.prefix + "_grants.csv", grantsT);
    hasHsel = LoadTable(r.prefix + "_hsel.csv", hselT);

    kpiStart = runT.R0("kpi_start", 3.0);
    kpiEnd = runT.R0("kpi_end", 1e9);
    maxNumTx = static_cast<int>(runT.R0("max_num_tx", 3));
    harqMode = runT.R0s("harq_mode");
    traffic = runT.R0s("traffic");
    pdbS = PdbSeconds(traffic);
    helperNodeStatic = static_cast<int>(runT.R0("helper", 0));

    BuildTxs();
    BuildTbs();
    LoadPkts();
    if (hasHsel)
    {
        const auto& tn = hselT.D("t_ndi");
        const auto& ap = hselT.D("applied");
        for (size_t i = 0; i < hselT.nrows; ++i)
        {
            m_hselBySlot[std::llround(tn[i] / kSlotS)] = static_cast<int>(ap[i]);
        }
    }
    for (size_t i = 0; i < nackT.nrows; ++i)
    {
        nacks.push_back({nackT.At("t", i), static_cast<int>(nackT.At("member", i)),
                         static_cast<int>(nackT.At("tx_rnti", i)), static_cast<int>(nackT.At("harq_id", i))});
    }
    AttributeNacks();
    return true;
}

double
Run::MemberCol(const std::string& col, int member) const
{
    if (!memberT.Has(col) || member < 1 || static_cast<size_t>(member) > memberT.nrows)
    {
        return NAN;
    }
    return memberT.D(col)[member - 1];
}

void
Run::BuildTxs()
{
    txs.clear();
    auto addSide = [&](const Table& t, int side) {
        for (size_t i = 0; i < t.nrows; ++i)
        {
            PTx x;
            x.side = side;
            x.row = static_cast<int>(i);
            x.t = t.At("t", i);
            x.slot = static_cast<int64_t>(t.At("slot_abs", i));
            x.rbStart = static_cast<int>(t.At("rb_start", i));
            x.rbLen = static_cast<int>(t.At("rb_len", i));
            x.harq = static_cast<int>(t.At("harq_id", i));
            x.ndi = static_cast<int>(t.At("ndi", i));
            x.rv = static_cast<int>(t.At("rv", i));
            x.txInTb = static_cast<int>(t.At("tx_in_tb", i));
            x.nSeq = static_cast<int>(t.At("n_seq", i));
            x.nNack = static_cast<int>(t.At("n_nack", i));
            x.decision = static_cast<int>(t.At("decision", i));
            x.nColl = static_cast<int>(t.At("n_coll", i));
            x.nearestColl = t.At("nearest_collider_m", i);
            x.nNackSent = static_cast<int>(t.At("n_nack_sent", i));
            x.node = side == 0 ? 0 : -1;
            x.status.fill(-1);
            x.sinr.fill(NAN);
            x.cond.fill(-1);
            x.dist.fill(NAN);
            txs.push_back(x);
        }
    };
    if (hasLtx)
    {
        addSide(ltxT, 0);
    }
    if (hasHtx)
    {
        addSide(htxT, 1);
    }
    std::stable_sort(txs.begin(), txs.end(), [](const PTx& a, const PTx& b) {
        if (a.t != b.t)
        {
            return a.t < b.t;
        }
        return a.side < b.side;
    });
    m_slotIdx[0].clear();
    m_slotIdx[1].clear();
    for (size_t u = 0; u < txs.size(); ++u)
    {
        m_slotIdx[txs[u].side][txs[u].slot] = static_cast<int>(u);
    }
    if (hasAtt)
    {
        const auto& side = attT.D("side");
        const auto& slot = attT.D("slot_abs");
        const auto& tb = attT.D("tb");
        const auto& mem = attT.D("member");
        const auto& st = attT.D("status");
        const auto& sinr = attT.D("sinr_db");
        const auto& cond = attT.D("cond");
        const auto& dist = attT.D("dist_m");
        for (size_t i = 0; i < attT.nrows; ++i)
        {
            const int s = static_cast<int>(side[i]);
            auto it = m_slotIdx[s].find(static_cast<int64_t>(slot[i]));
            if (it == m_slotIdx[s].end())
            {
                continue;
            }
            PTx& x = txs[it->second];
            const int m = static_cast<int>(mem[i]) - 1;
            if (m < 0 || m >= kMembers)
            {
                continue;
            }
            x.hasAtt = true;
            x.status[m] = static_cast<int8_t>(st[i]);
            x.sinr[m] = static_cast<float>(sinr[i]);
            x.cond[m] = static_cast<int8_t>(cond[i]);
            x.dist[m] = static_cast<float>(dist[i]);
            x.tb = static_cast<int64_t>(tb[i]);
        }
    }
    else
    {
        // no _att: infer the leader TB from ndi / harq id (helper copies stay unknown)
        std::map<int, int64_t> cur;
        for (auto& x : txs)
        {
            if (x.side != 0)
            {
                continue;
            }
            if (x.ndi == 1 || !cur.count(x.harq))
            {
                cur[x.harq] = x.row;
            }
            x.tb = cur[x.harq];
        }
    }
    // node of a helper copy: the non-leader platoon (dst 254) transmitter in the same slot
    if (hasAlltx)
    {
        std::unordered_map<int64_t, std::vector<size_t>> bySlot;
        const auto& slot = alltxT.D("slot_abs");
        const auto& node = alltxT.D("node");
        const auto& dst = alltxT.D("dst");
        for (size_t i = 0; i < alltxT.nrows; ++i)
        {
            if (dst[i] == 254 && node[i] != 0)
            {
                bySlot[static_cast<int64_t>(slot[i])].push_back(i);
            }
        }
        const auto& rbs = alltxT.D("rb_start");
        const auto& rbl = alltxT.D("rb_len");
        for (auto& x : txs)
        {
            if (x.side != 1)
            {
                continue;
            }
            auto it = bySlot.find(x.slot);
            if (it == bySlot.end())
            {
                continue;
            }
            for (size_t i : it->second)
            {
                if (static_cast<int>(rbs[i]) == x.rbStart && static_cast<int>(rbl[i]) == x.rbLen)
                {
                    x.node = static_cast<int>(node[i]);
                }
            }
        }
    }
}

void
Run::BuildTbs()
{
    tbs.clear();
    for (size_t u = 0; u < txs.size(); ++u)
    {
        const PTx& x = txs[u];
        if (x.tb < 0)
        {
            continue;
        }
        Tb& b = tbs[x.tb];
        b.id = x.tb;
        b.tx.push_back(static_cast<int>(u));
        if (x.side == 0 && x.ndi == 1 && b.harq < 0)
        {
            b.harq = x.harq;
        }
    }
}

void
Run::LoadPkts()
{
    for (auto& v : pkts)
    {
        v.clear();
    }
    if (pktT.nrows == 0)
    {
        return;
    }
    const auto& seq = pktT.D("seq");
    const auto& tTx = pktT.D("t_tx");
    const auto& size = pktT.D("size");
    const auto& mem = pktT.D("member");
    const auto& rx = pktT.D("rx");
    const auto& dly = pktT.D("delay_ms");
    const auto& cause = pktT.D("cause");
    const auto& nAtt = pktT.D("n_att");
    const auto& nColl = pktT.D("n_att_coll");
    const auto& nFail = pktT.D("n_att_fail");
    const auto& dist = pktT.D("dist_m");
    const auto& cond = pktT.D("cond");
    const auto& sinr = pktT.D("best_sinr_db");
    for (size_t i = 0; i < pktT.nrows; ++i)
    {
        const int m = static_cast<int>(mem[i]) - 1;
        if (m < 0 || m >= kMembers)
        {
            continue;
        }
        pkts[m].push_back({static_cast<int64_t>(seq[i]), tTx[i], static_cast<int>(size[i]), rx[i] != 0, dly[i],
                           static_cast<int>(cause[i]), static_cast<int>(nAtt[i]), static_cast<int>(nColl[i]),
                           static_cast<int>(nFail[i]), dist[i], static_cast<int>(cond[i]), sinr[i]});
    }
    // generation time of each TB: first packet carried (cumulative n_seq of the ndi=1 transmissions)
    std::unordered_map<int64_t, double> tOfSeq;
    for (const auto& p : pkts[0])
    {
        tOfSeq[p.seq] = p.tTx;
    }
    int64_t cum = 0;
    for (const PTx& x : txs)
    {
        if (x.side != 0 || x.ndi != 1)
        {
            continue;
        }
        auto it = tbs.find(x.tb);
        if (it != tbs.end())
        {
            it->second.firstSeq = cum;
            auto ts = tOfSeq.find(cum);
            if (ts != tOfSeq.end())
            {
                it->second.tGen = ts->second;
            }
        }
        cum += x.nSeq;
    }
}

std::vector<int64_t>
Run::WindowTbs() const
{
    std::vector<int64_t> out;
    for (const auto& [id, b] : tbs)
    {
        for (int u : b.tx)
        {
            if (txs[u].side == 0)
            {
                if (InWin(txs[u].t))
                {
                    out.push_back(id);
                }
                break;
            }
        }
    }
    return out;
}

long
Run::NewTbsInWindow() const
{
    long n = 0;
    for (const PTx& x : txs)
    {
        n += (x.side == 0 && x.txInTb == 1 && InWin(x.t)) ? 1 : 0;
    }
    return n;
}

bool
Run::DecodedBy(const Tb& tb, int k, int member) const
{
    for (int i = 0; i <= k && i < static_cast<int>(tb.tx.size()); ++i)
    {
        const PTx& x = txs[tb.tx[i]];
        if (IsSelf(x, member))
        {
            continue;
        }
        if (x.status[member - 1] == 1)
        {
            return true;
        }
    }
    return false;
}

int
Run::HelperOf(const PTx& initial) const
{
    auto it = m_hselBySlot.find(initial.slot);
    if (it == m_hselBySlot.end())
    {
        // selector arms: a TB sent under a single-slot SPS grant hands no slot, the selector is not run and no helper is named
        return hasHsel ? 0 : helperNodeStatic;
    }
    return it->second;
}

int
Run::EffHarq(const PTx& x) const
{
    if (x.side == 0)
    {
        return x.harq;
    }
    auto it = tbs.find(x.tb);
    return (it != tbs.end() && it->second.harq >= 0) ? it->second.harq : x.harq;
}

void
Run::AttributeNacks()
{
    nackTx.assign(nacks.size(), -1);
    nackAmb.assign(nacks.size(), false);
    nackUnmatched = nackAmbiguous = 0;
    // index: (rnti, effective harq) -> tx indices in time order
    std::map<std::pair<int, int>, std::vector<int>> by;
    for (size_t u = 0; u < txs.size(); ++u)
    {
        if (txs[u].node < 0)
        {
            continue;
        }
        by[{RntiOfNode(txs[u].node), EffHarq(txs[u])}].push_back(static_cast<int>(u));
    }
    for (size_t i = 0; i < nacks.size(); ++i)
    {
        const NackRow& n = nacks[i];
        auto it = by.find({n.rnti, n.harq});
        if (it == by.end())
        {
            ++nackUnmatched;
            continue;
        }
        int match = -1;
        int within32 = 0;
        std::set<int64_t> tbsIn32;
        for (int u : it->second)
        {
            const PTx& x = txs[u];
            const double dt = n.t - x.t;
            if (dt > 0 && dt <= kSlotS + 1e-7)
            {
                match = u;
            }
            if (dt > 0 && dt <= 32 * kSlotS)
            {
                ++within32;
                tbsIn32.insert(x.tb);
            }
        }
        if (match < 0)
        {
            ++nackUnmatched;
            continue;
        }
        nackTx[i] = match;
        if (tbsIn32.size() > 1)
        {
            ++nackAmbiguous;
            nackAmb[i] = true;
        }
        if (n.member >= 1 && n.member <= kMembers)
        {
            txs[match].nackMask |= static_cast<uint16_t>(1u << (n.member - 1));
        }
        (void)within32;
    }
}

int
Run::FindTx(int side, int64_t slot) const
{
    auto it = m_slotIdx[side].find(slot);
    return it == m_slotIdx[side].end() ? -1 : it->second;
}

std::vector<TbView>
WindowTbViews(const Run& r)
{
    std::vector<TbView> out;
    for (const auto& [id, tb] : r.tbs)
    {
        TbView v;
        v.id = id;
        v.tb = &tb;
        v.tGen = tb.tGen;
        for (int u : tb.tx)
        {
            const PTx* x = &r.txs[u];
            v.all.push_back(x);
            (x->side == 0 ? v.leader : v.helper).push_back(x);
            if (x->side == 0 && x->txInTb == 1 && !v.initial)
            {
                v.initial = x;
            }
        }
        if (!v.initial || !r.InWin(v.initial->t))
        {
            continue;
        }
        const std::string retx = r.ref.Retx();
        if (retx == "R4" || retx == "R5" || retx == "R6")
        {
            v.helperNode = r.HelperOf(*v.initial);
        }
        out.push_back(std::move(v));
    }
    return out;
}

} // namespace ptest

namespace ptest
{
int
Run::GrantSlotsAt(double t) const
{
    if (!hasGrants)
    {
        return -1;
    }
    const auto& tt = grantsT.D("t");
    const auto& nd = grantsT.D("node");
    const auto& sp = grantsT.D("sps");
    const auto& ev = grantsT.S("event");
    const auto& sl = grantsT.D("slots_or_counter");
    int best = -1;
    for (size_t i = 0; i < grantsT.nrows; ++i)
    {
        if (nd[i] == 0 && ev[i] == "c" && sp[i] != 0 && tt[i] <= t + 1e-9)
        {
            best = static_cast<int>(sl[i]);
        }
    }
    return best;
}

void
Run::GrantSlotStats(long& all, long& lessThanMax, long& single) const
{
    if (!hasGrants)
    {
        return;
    }
    const auto& nd = grantsT.D("node");
    const auto& sp = grantsT.D("sps");
    const auto& ev = grantsT.S("event");
    const auto& sl = grantsT.D("slots_or_counter");
    for (size_t i = 0; i < grantsT.nrows; ++i)
    {
        if (nd[i] == 0 && ev[i] == "c" && sp[i] != 0)
        {
            ++all;
            lessThanMax += (sl[i] < maxNumTx) ? 1 : 0;
            single += (sl[i] <= 1) ? 1 : 0;
        }
    }
}
} // namespace ptest
