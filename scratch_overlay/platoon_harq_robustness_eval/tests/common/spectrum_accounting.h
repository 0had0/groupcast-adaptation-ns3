// spectrum_accounting.h -- transmitted AND reserved/announced PSSCH resources of a run, per node and per resource origin
// (TEST_PLAN decision D-2).  Reusable for later studies:
//   LeaderSps        the helper relays in resources the leader reserved with SB-SPS   (S1, --leaderSps=true)
//   LeaderDynamic    ... reserved with a dynamic grant                                (S1, --leaderSps=false)
//   HelperOwnDynamic the helper uses its own dynamic resources                        (S3 ownRes; API only, not run)
//
// Transmitted: every PSSCH of the KPI window from _alltx.csv (dst 254 = platoon; node 0 = leader, others = helper;
// the rest is background), in subchannel-slots (rb_len / 10).
// Reserved: from the LEADER grant events of _grants.csv (never from the res_* columns of _run.csv, F02):
//   'c' (created): sb_start, sb_len, slots_or_counter = GrantInfo::nSelected (slots reserved per period = initial + reserved
//   retransmissions), rri_ms, first_slot, extra = SL_RESOURCE_RESELECTION_COUNTER (SPS);  'k' (kept): the SPS resource is kept
//   for a new counter (slots_or_counter = the new counter);  'm' (misfit): the TB does not fit, reselection or enlargement.
//   An SPS chain starts at a 'c' and repeats every rri_ms from first_slot until the next 'c' of the node (reselection); a
//   dynamic grant ('c' with sps=0) reserves one period.  Every period in force in the window reserves
//   nSelected x sb_len subchannel-slots.
// Classification of the reserved subchannel-slots of a period (per TB, because only the first slot of a period is logged):
//   used_leader / used_helper / reclaimed_leader (leader transmission in a slot handed to the helper, helper arms)
//   unused_ack (R2/R3: retransmission slots cancelled by ACK) / unused_no_trigger (R5: no NACK, R6: every other member ACKed)
//   unused_helper_not_decoded / unused_empty_period (SPS period without data) / unused_reselection (the one announced
//   period after a reselection) / unused_other (residual; includes R1 blind slots that were not used).
#pragma once

#include "common/run_outputs.h"

#include <map>
#include <string>

namespace ptest
{

enum class ResOrigin
{
    LeaderSps,
    LeaderDynamic,
    HelperOwnDynamic
};
const char* OriginName(ResOrigin o);

struct ReservedBreakdown
{
    double reserved{0};
    double usedLeader{0}, usedHelper{0}, reclaimedLeader{0};
    double unusedAck{0}, unusedNoTrigger{0}, unusedHelperNotDecoded{0}, unusedEmptyPeriod{0}, unusedReselection{0},
        unusedOther{0};
    long periods{0}, periodsWithData{0}, reselections{0}, dynamicGrants{0};
    double ClassifiedSum() const
    {
        return usedLeader + usedHelper + reclaimedLeader + unusedAck + unusedNoTrigger + unusedHelperNotDecoded +
               unusedEmptyPeriod + unusedReselection + unusedOther;
    }
};

struct SpectrumAcct
{
    ResOrigin origin{ResOrigin::LeaderSps};
    bool ok{false};
    std::string why;        //!< reason when !ok
    double winS{0};
    // transmitted subchannel-slots in the KPI window
    double txLeader{0}, txHelper{0}, txBackground{0};
    std::map<int, double> txByNode;  //!< platoon (dst 254) nodes
    long nLeaderTx{0}, nHelperTx{0};
    // reserved
    ReservedBreakdown res;
    long newTbs{0};         //!< leader TBs started in the window
    long reservedCountCheck{0};
    std::string gapNote;    //!< logging gaps to report as INFO
};

SpectrumAcct ComputeSpectrum(const Run& r);

/// D-2 known gap, to be reported as INFO: helper-own grants and SCI-1A announcements are not logged.
std::string HelperOwnLoggingGap();

} // namespace ptest
