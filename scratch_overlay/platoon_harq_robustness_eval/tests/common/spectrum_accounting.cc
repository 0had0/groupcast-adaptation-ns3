#include "common/spectrum_accounting.h"

#include <algorithm>
#include <cmath>
#include <unordered_map>

namespace ptest
{

const char*
OriginName(ResOrigin o)
{
    switch (o)
    {
    case ResOrigin::LeaderSps: return "leader_sps";
    case ResOrigin::LeaderDynamic: return "leader_dynamic";
    case ResOrigin::HelperOwnDynamic: return "helper_own_dynamic";
    }
    return "?";
}

std::string
HelperOwnLoggingGap()
{
    return "the helper's own grants and SCI-1A announcements are not logged by the simulator (members are skipped when the "
           "GrantCreated trace is connected, platoon_harq_robustness_eval.cc:1876-1879, and there is no SCI-1A file); "
           "case (c) helper-own dynamic resources (S3 ownRes) needs a logging flag before its reserved resources can be "
           "accounted. Recommended: write helper grants to _grants.csv and the SCI-1A reservations (slot, subchannels, gaps) "
           "of every UE to a new file.";
}

namespace
{
struct Chain
{
    double tc;
    bool sps;
    int64_t rriSlots;
    int64_t first;
    int sbLen;
    int nSel;
};
} // namespace

SpectrumAcct
ComputeSpectrum(const Run& r)
{
    SpectrumAcct a;
    a.origin = r.ref.leaderSps ? ResOrigin::LeaderSps : ResOrigin::LeaderDynamic;
    a.winS = r.kpiEnd - r.kpiStart;
    a.gapNote = HelperOwnLoggingGap();
    if (!r.hasAlltx)
    {
        a.why = "no _alltx.csv (needs --writeAllTx=true)";
        return a;
    }
    const int64_t s0 = std::llround(r.kpiStart / kSlotS), s1 = std::llround(r.kpiEnd / kSlotS);

    // ---- transmitted ---------------------------------------------------------------------------------------------
    {
        const auto& slot = r.alltxT.D("slot_abs");
        const auto& node = r.alltxT.D("node");
        const auto& rbl = r.alltxT.D("rb_len");
        const auto& dst = r.alltxT.D("dst");
        const auto& x = r.alltxT.D("x");
        const auto& y = r.alltxT.D("y");
        for (size_t i = 0; i < r.alltxT.nrows; ++i)
        {
            const int64_t sl = static_cast<int64_t>(slot[i]);
            if (sl < s0 || sl >= s1 || std::hypot(x[i], y[i]) > 5.0e4)
            {
                continue;
            }
            const double sc = rbl[i] / kSubchPrbs;
            if (dst[i] == 254)
            {
                a.txByNode[static_cast<int>(node[i])] += sc;
                if (node[i] == 0)
                {
                    a.txLeader += sc;
                    ++a.nLeaderTx;
                }
                else
                {
                    a.txHelper += sc;
                    ++a.nHelperTx;
                }
            }
            else
            {
                a.txBackground += sc;
            }
        }
    }
    for (const PTx& x : r.txs)
    {
        a.newTbs += (x.side == 0 && x.txInTb == 1 && r.InWin(x.t)) ? 1 : 0;
    }

    // ---- reserved (leader grants) -------------------------------------------------------------------------------
    if (!r.hasGrants)
    {
        a.why = "no _grants.csv (needs --writeGrants=true)";
        return a;
    }
    std::vector<Chain> chains;
    {
        const auto& t = r.grantsT.D("t");
        const auto& node = r.grantsT.D("node");
        const auto& ev = r.grantsT.S("event");
        const auto& sps = r.grantsT.D("sps");
        const auto& rri = r.grantsT.D("rri_ms");
        const auto& first = r.grantsT.D("first_slot");
        const auto& sbl = r.grantsT.D("sb_len");
        const auto& nsel = r.grantsT.D("slots_or_counter");
        for (size_t i = 0; i < r.grantsT.nrows; ++i)
        {
            if (node[i] != 0 || ev[i] != "c")
            {
                continue;
            }
            Chain c;
            c.tc = t[i];
            c.sps = sps[i] != 0;
            c.rriSlots = static_cast<int64_t>(std::llround(rri[i] / 1000.0 / kSlotS));
            c.first = static_cast<int64_t>(first[i]);
            c.sbLen = static_cast<int>(sbl[i]);
            c.nSel = static_cast<int>(nsel[i]);
            chains.push_back(c);
        }
    }
    // leader TB (ndi=1) per slot
    std::unordered_map<int64_t, int64_t> tbAtSlot;
    for (const PTx& x : r.txs)
    {
        if (x.side == 0 && x.txInTb == 1)
        {
            tbAtSlot[x.slot] = x.tb;
        }
    }
    // helper named per TB (hsel row at the TB's ndi=1 time)
    auto namedHelper = [&](const PTx& first) -> int {
        if (!r.hasHsel)
        {
            return r.helperNodeStatic;
        }
        const auto& tn = r.hselT.D("t_ndi");
        const auto& ap = r.hselT.D("applied");
        for (size_t i = 0; i < r.hselT.nrows; ++i)
        {
            if (std::fabs(tn[i] - first.t) < 0.0004)
            {
                return static_cast<int>(ap[i]);
            }
        }
        return r.helperNodeStatic;
    };
    const std::string retx = r.ref.Retx();
    const bool helperArm = (retx == "R4" || retx == "R5" || retx == "R6");

    ReservedBreakdown& R = a.res;
    auto periodWithData = [&](const Chain& c, int64_t slot) {
        const double unit = static_cast<double>(c.sbLen);
        const double resv = c.nSel * unit;
        R.reserved += resv;
        ++R.periods;
        auto it = tbAtSlot.find(slot);
        if (it == tbAtSlot.end())
        {
            R.unusedEmptyPeriod += resv;
            return;
        }
        ++R.periodsWithData;
        auto tbIt = r.tbs.find(it->second);
        if (tbIt == r.tbs.end())
        {
            R.unusedOther += resv;
            return;
        }
        const Tb& tb = tbIt->second;
        int nTx = 0, nLeader = 0;
        const PTx* initial = nullptr;
        const PTx* lastLeader = nullptr;
        const PTx* lastAny = nullptr;
        for (int u : tb.tx)
        {
            const PTx& x = r.txs[u];
            ++nTx;
            if (!lastAny || x.t > lastAny->t)
            {
                lastAny = &x;
            }
            const double sc = x.subch();
            if (x.side == 1)
            {
                R.usedHelper += sc;
            }
            else
            {
                ++nLeader;
                if (x.txInTb == 1)
                {
                    R.usedLeader += sc;
                    initial = &x;
                }
                else if (helperArm)
                {
                    R.reclaimedLeader += sc;
                }
                else
                {
                    R.usedLeader += sc;
                }
                lastLeader = &x;
            }
        }
        const int unusedSlots = std::max(0, c.nSel - nTx);
        if (unusedSlots == 0 || !initial)
        {
            return;
        }
        const double unusedSc = unusedSlots * unit;
        if (retx == "R2" || retx == "R3")
        {
            if (lastLeader && lastLeader->decision == 1)
            {
                R.unusedAck += unusedSc;
            }
            else
            {
                R.unusedOther += unusedSc;
            }
        }
        else if (retx == "R1")
        {
            R.unusedOther += unusedSc;
        }
        else
        {
            const int h = namedHelper(*initial);
            const bool helperDecoded = (h >= 1 && h <= kMembers && initial->status[h - 1] == 1);
            // the relay is not triggered when the LAST transmission of the TB (leader or copy) drew no NACK (R5) / was declared a success (R6)
            const PTx& endTx = lastAny ? *lastAny : *initial;
            bool notTriggered = false;
            if (retx == "R5")
            {
                notTriggered = endTx.nNackSent == 0;
            }
            else if (retx == "R6")
            {
                notTriggered = endTx.decision == 1;
            }
            if (retx == "R4")
            {
                (helperDecoded ? R.unusedOther : R.unusedHelperNotDecoded) += unusedSc;
            }
            else if (notTriggered)
            {
                // R6: success declared (every other member ACKed, nobody NACKed) -> the remaining handed slots are cancelled by the ACKs;
                // R5: no NACK was sent -> the relay is not triggered
                (retx == "R6" ? R.unusedAck : R.unusedNoTrigger) += unusedSc;
            }
            else if (!helperDecoded)
            {
                R.unusedHelperNotDecoded += unusedSc;
            }
            else
            {
                R.unusedOther += unusedSc;
            }
        }
    };

    for (size_t i = 0; i < chains.size(); ++i)
    {
        const Chain& c = chains[i];
        const bool hasNext = i + 1 < chains.size();
        const int64_t nextFirst = hasNext ? chains[i + 1].first : INT64_MAX;
        if (!c.sps || c.rriSlots <= 0)
        {
            if (c.first >= s0 && c.first < s1)
            {
                ++R.dynamicGrants;
                periodWithData(c, c.first);
            }
            continue;
        }
        for (int64_t slot = c.first;; slot += c.rriSlots)
        {
            if (slot >= nextFirst || slot >= s1)
            {
                break;
            }
            if (slot < s0)
            {
                continue;
            }
            periodWithData(c, slot);
        }
        if (hasNext)
        {
            // the announced period right after the reselection that will never be used
            const int64_t tcSlot = std::llround(chains[i + 1].tc / kSlotS);
            int64_t k = (tcSlot <= c.first) ? 0 : (tcSlot - c.first + c.rriSlots - 1) / c.rriSlots;
            const int64_t sNext = c.first + k * c.rriSlots;
            if (sNext >= nextFirst && sNext >= s0 && sNext < s1)
            {
                ++R.reselections;
                const double resv = c.nSel * static_cast<double>(c.sbLen);
                R.reserved += resv;
                R.unusedReselection += resv;
            }
        }
    }
    // residual: reserved - classified (negative = the TB used more than the grant reserved)
    const double resid = R.reserved - R.ClassifiedSum();
    if (std::fabs(resid) > 1e-9)
    {
        R.unusedOther += resid;
    }
    a.ok = true;
    return a;
}

} // namespace ptest
