// analysis_kit.h -- shared analyses of the G tests (G01-G08): attempt classification (effective / redundant / futile), per member-TB loss
// classification (why a member did not receive a packet), selection of the "worst receiver".
#pragma once

#include "common/run_outputs.h"

#include <map>
#include <string>
#include <vector>

namespace ptest
{

// ---- G01: attempt effectiveness ------------------------------------------------------------------------------------------------------------------
enum class Eff
{
    Self = 0,      //!< the member is the transmitter of this copy (not a receiver)
    Effective,     //!< not decoded before, decodes now
    Redundant,     //!< already decoded before this attempt
    FutileSci,     //!< not decoded, SCI missed (status 0)
    FutileColl,    //!< not decoded, corrupt, the attempt collided (n_coll > 0)
    FutileChannel  //!< not decoded, corrupt, no collision
};
const char* EffName(Eff e);

/// classification of attempt position k (>= 1) of the TB (time ordered over leader and helper transmissions) for member m (1..8)
Eff ClassifyAttempt(const Run& r, const TbView& tv, size_t k, int m);

/// one row per (TB, member) with the app-level outcome and the reason of a loss
enum class Cat
{
    Delivered = 0,
    NoTx,
    StopSciMissed,        //!< T3: Option 1, the member missed the SCI on its last attempt, so no NACK: the TB ended with budget left
    StopSci2NoNack,       //!< T3c: Option 1, the member decoded the SCI-1A, its PSSCH was corrupt but it sent no NACK (SCI-2A corrupt: no feedback possible)
    StopHelperBlindSpot,  //!< G08 H0a: R6, the named helper missed the SCI and the leader declared success
    StopHelperNotDecoded, //!< R4-R6: the helper did not decode, so no copy was sent (F18)
    StopStale,            //!< T6: a decision was stale (HARQ id reused), feedback dropped
    StopMcr,              //!< T4: Option 1, member beyond the MCR
    StopPdb,              //!< T2: the next feasible retransmission lies beyond the PDB
    StopOther,            //!< stopped early for another reason
    Exhausted,            //!< all maxNumTx transmissions were sent and the member still failed (channel / collision: the baseline loss)
    N
};
const char* CatName(Cat c);

struct PairOutcome
{
    int64_t tb;
    int member;
    Cat cat;
    int nTx;
    // conditions of the first transmission towards the member (D-4)
    int cond{-1};         //!< 0 NLOS, 1 NLOSv, 2 LOS
    float dist{NAN};      //!< leader -> member distance (m)
    float sinr{NAN};      //!< SINR of the first transmission at the member (dB), NaN if the SCI was missed
    bool coll{false};     //!< the first transmission collided
    int status0{-1};      //!< status of the member on the first transmission
    bool everSciMissed{false}; //!< the member missed the SCI on some attempt before it decoded (or never decoded)
    int deliverIdx{-1};   //!< index (in time order) of the transmission that delivered the TB to the member, -1 if never
    double tDeliverOffsetMs{NAN}; //!< time of that transmission relative to the first one
    bool delivered() const { return cat == Cat::Delivered; }
};
/// classify every (window TB, member) pair with a packet in the KPI window
void ClassifyPairs(const Run& r, std::vector<PairOutcome>& out);

/// member (1..8) with the lowest prr_pdb of a run (ties -> the farther member)
int WorstMemberByPrr(const Run& r);

} // namespace ptest
