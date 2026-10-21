// F02_dynamic_behaviour (W: SYN_SPS leaderSps=false; C: SPSDYN): dynamic grants: one grant per TB, no reservation period, every
// transmission inside [t_gen + T1, t_gen + min(T2, PDB)].
#include "common/csv.h"
#include "common/harness.h"
#include "common/run_outputs.h"
#include "common/runsets.h"
#include "common/stats.h"

#include <cmath>
#include <map>

using namespace ptest;

int
main()
{
    TestReport T("F02", "dynamic_behaviour", "F");
    T.Summary("With --leaderSps=false every TB has its own dynamic grant (sps = 0, no reservation period, no keep events) and all its "
              "transmissions lie in the selection window [T1, min(T2, PDB)] after generation.");
    T.AddRef("TS 38.214 8.1.4 (dynamic selection inside [n+T1, n+T2]), TS 38.321 5.22.1.1 [V]");
    ViolationLog V;
    const double T1 = 2, T2 = 33; // slots (nr_sl_stack.cc: T1 = 2, T2 = 33)
    for (const std::string rs : {"SYN_SPS", "SPSDYN"})
    {
        const auto all = LoadManifest(rs);
        if (all.empty()) { T.Skip(rs + " data", rs + " manifest not present (campaign stage not run yet)"); continue; }
        T.AddRunset(rs);
        long runs = 0, grantsBad = 0, keepBad = 0, nGrantTb = 0, tbs = 0, outWin = 0, txN = 0, grantCountMismatch = 0;
        for (const auto& rr : all)
        {
            if (rr.leaderSps) continue;
            Run r;
            if (!r.Load(rr) || !r.hasGrants) continue;
            ++runs;
            T.AddNRuns(1);
            const auto& node = r.grantsT.D("node");
            const auto& ev = r.grantsT.S("event");
            const auto& sps = r.grantsT.D("sps");
            const auto& rri = r.grantsT.D("rri_ms");
            const auto& t = r.grantsT.D("t");
            long gAll = 0, gWin = 0;
            for (size_t i = 0; i < r.grantsT.nrows; ++i)
            {
                if (node[i] != 0) continue;
                if (ev[i] == "c")
                {
                    ++gAll;
                    gWin += (t[i] >= r.kpiStart && t[i] < r.kpiEnd);
                    if (sps[i] != 0 || rri[i] != 0) { ++grantsBad; V.Add("dynamic grant: sps = 0 and rri 0", rs + "/" + rr.arm + "/" + rr.tag + " row " + std::to_string(i)); }
                }
                if (ev[i] == "k") ++keepBad;
            }
            const double pdbSlots = r.pdbS / kSlotS;
            const double hi = std::min(T2, pdbSlots);
            long nNew = 0;
            for (const TbView& tv : WindowTbViews(r))
            {
                ++tbs;
                ++nNew;
                if (std::isnan(tv.tGen)) continue;
                for (const PTx* x : tv.all)
                {
                    ++txN;
                    const double dSlots = (x->t - tv.tGen) / kSlotS;
                    // 1 slot of slack: the packet is generated inside a slot and the grant is published at the next boundary
                    if (dSlots < T1 - 1.0 - 1e-6 || dSlots > hi + 1.0 + 1e-6)
                    {
                        ++outWin;
                        V.Add("transmission inside [t_gen + T1, t_gen + min(T2, PDB)]", rs + "/" + rr.arm + "/" + rr.tag + " tb " + FmtI(tv.id) + " delta " + Fmt(dSlots, 4) + " slots");
                    }
                }
            }
            nGrantTb += gWin;
            // one grant per TB: created grants in the window equal the new TBs (+-2 boundary)
            if (std::labs(gWin - nNew) > 2) ++grantCountMismatch;
        }
        if (runs == 0) { T.Skip(rs + " dynamic runs", "no leaderSps=false runs in this manifest"); continue; }
        T.Check(rs + ": every leader grant is dynamic (sps = 0, rri_ms = 0)", grantsBad == 0, Opt().Exp("0").Obs(FmtI(grantsBad)).Tol("0").Sev("M"));
        T.Check(rs + ": no SPS keep events", keepBad == 0, Opt().Exp("0").Obs(FmtI(keepBad)).Tol("0").Sev("M"));
        T.Check(rs + ": one grant per TB (created grants in the window == new TBs +-2 per run)", grantCountMismatch == 0, Opt().Exp("0 runs differ").Obs(FmtI(grantCountMismatch) + " of " + FmtI(runs)).Tol("2 per run").Sev("M"));
        T.Check(rs + ": every transmission lies in [t_gen + T1, t_gen + min(T2, PDB)] (T1 = 2, T2 = 33 slots, +-1 slot slack)", outWin == 0,
                Opt().Exp("0 of " + FmtI(txN)).Obs(FmtI(outWin)).Tol("0 (1 slot slack)").Sev("H").Fnd("F23"));
    }
    const std::string art = V.Write("F02_violations");
    if (!art.empty()) T.Artefact(art);
    return T.Finish();
}
