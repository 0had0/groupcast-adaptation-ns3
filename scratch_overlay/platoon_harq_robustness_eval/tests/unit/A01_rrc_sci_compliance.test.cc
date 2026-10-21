// A01_rrc_sci_compliance (U): RRC parameter ranges of the study configuration, RRI list, SCI-1A / SCI-2A header
// round trips and cast-type codes against TS 38.331 / TS 38.212 (TEST_PLAN section 4.1, 4.2, 8 "A01").
#include "common/harness.h"

#include "common/stack/nr_sl_stack.h"

#include "ns3/core-module.h"
#include "ns3/lte-sl-tft.h"
#include "ns3/nr-sl-sci-f1a-header.h"
#include "ns3/nr-sl-sci-f2a-header.h"
#include "ns3/packet.h"

#include <fstream>
#include <set>
#include <sstream>

using namespace ns3;
using ptest::Opt;
using ptest::TestReport;

namespace
{

struct Param
{
    std::string name;
    std::string value;
    std::string allowed;
    bool inSet;
    std::string note;
};

bool
InSet(double v, const std::vector<double>& s)
{
    for (double x : s)
    {
        if (std::fabs(x - v) < 1e-9)
        {
            return true;
        }
    }
    return false;
}

// SCI-1A with the study's mandatory fields
NrSlSciF1aHeader
MakeF1a(uint8_t nmax, uint8_t g1, uint8_t g2)
{
    NrSlSciF1aHeader h;
    h.SetPriority(1);
    h.SetMcs(14);
    h.SetSciStage2Format(NrSlSciF1aHeader::SciFormat2A);
    h.SetSlResourceReservePeriod(100);
    h.SetTotalSubChannels(5);
    h.SetIndexStartSubChannel(1);
    h.SetLengthSubChannel(2);
    h.SetSlMaxNumPerReserve(nmax);
    if (nmax >= 2)
    {
        h.SetGapReTx1(g1);
        h.SetIndexStartSbChReTx1(2);
    }
    if (nmax == 3)
    {
        h.SetGapReTx2(g2);
        h.SetIndexStartSbChReTx2(3);
    }
    return h;
}

bool
RoundTripF1a(const NrSlSciF1aHeader& h)
{
    Ptr<Packet> p = Create<Packet>();
    p->AddHeader(h);
    NrSlSciF1aHeader d;
    p->PeekHeader(d);
    return d == h && h.GetSerializedSize() == p->GetSize();
}

} // namespace

int
main()
{
    TestReport T("A01", "rrc_sci_compliance", "A");
    T.Summary("Study RRC values lie in the TS 38.331 sets; SCI-1A/2A headers round-trip; cast-type codes match "
              "TS 38.212 Table 8.4.1.1-1; the CoopInfo side data is not charged on the air interface (DV6).");
    T.AddRef("TS 38.331 sl-PSFCH-Period, sl-MinTimeGapPSFCH, sl-MaxNumPerReserve, sl-MaxTxTransNumPSSCH, "
             "sl-ProbResourceKeep, sl-ResourceReservePeriod, sl-SensingWindow, sl-SelectionWindow [V]");
    T.AddRef("TS 38.212 8.3.1.1 (SCI 1-A), 8.4.1.1 (SCI 2-A, Table 8.4.1.1-1 cast type) [V]");

    // ---- 1. study configuration vs RRC sets -----------------------------------------------------------------
    SlStackParams defaults; // stack defaults (documented below)
    const std::vector<std::string> rriStudy = {"0", "10", "20", "50", "100", "200", "300",
                                               "400", "500", "600", "700", "800", "900", "1000"};
    std::vector<Param> table;
    auto add = [&](const std::string& n, double v, const std::vector<double>& set, const std::string& setTxt,
                   const std::string& note) {
        table.push_back({n, ptest::Fmt(v), setTxt, InSet(v, set), note});
    };
    add("sl-PSFCH-Period", 1, {0, 1, 2, 4}, "{0,1,2,4} slots", "CLI default psfchPeriod=1");
    add("sl-MinTimeGapPSFCH", 3, {2, 3}, "{2,3} slots", "literal 3 at nr_sl_stack.cc:394");
    add("sl-MaxNumPerReserve", 3, {2, 3}, "{2,3}", "CLI default maxNumPerReserve=3 (stack default is 1)");
    {
        std::vector<double> r32;
        for (int i = 1; i <= 32; ++i) r32.push_back(i);
        add("sl-MaxTxTransNumPSSCH", 3, r32, "1..32", "BASE_FLAGS --maxNumTx=3 (total transmissions incl. the first)");
    }
    add("sl-ProbResourceKeep", 0.8, {0, 0.2, 0.4, 0.6, 0.8}, "{0,0.2,0.4,0.6,0.8}", "BASE_FLAGS");
    add("sl-SensingWindow", 100, {100, 1100}, "{100,1100} ms", "nr_sl_stack.cc:360");
    add("sl-SelectionWindow(T2min)", 5, {1, 5, 10, 20}, "{1,5,10,20}*2^mu slots", "nr_sl_stack.cc:361");
    for (const auto& p : table)
    {
        T.Check("rrc/" + p.name + "=" + p.value + " in " + p.allowed, p.inSet,
                Opt().Exp(p.allowed).Obs(p.value).Ref("TS 38.331 [V]").Note(p.note).Sev("L"));
    }
    // out-of-set stack defaults that the study overrides (INFO)
    {
        std::ostringstream os;
        os << "SlStackParams defaults: maxNumPerReserve=" << +defaults.maxNumPerReserve << ", maxNumTx="
           << +defaults.maxNumTx << ", psfchPeriod=" << defaults.psfchPeriod;
        const bool nonCompliant = !(defaults.maxNumPerReserve == 2 || defaults.maxNumPerReserve == 3);
        T.Info("rrc/stack default sl-MaxNumPerReserve", Opt()
                                                        .Exp("{2,3}")
                                                        .Obs(os.str())
                                                        .Note(nonCompliant ? "stack default is non-compliant; the study overrides it with 3 "
                                                                             "(platoon_harq_robustness_eval CLI default maxNumPerReserve=3)"
                                                                           : "stack default compliant")
                                                        .Ref("TS 38.331 sl-MaxNumPerReserve"));
    }
    T.Info("rrc/not modelled", Opt().Note("sl-NumMuxCS-Pair {1,2,3,6} and sl-PSFCH-CandidateResourceType "
                                          "{startSubCH, allocSubCH} are not modelled (PSFCH is a logical message, DV1)"));

    // ---- 2. RRI list ------------------------------------------------------------------------------------------
    {
        bool allLegal = true;
        std::string bad;
        for (const auto& s : rriStudy)
        {
            const int v = std::stoi(s);
            const bool legal = (v == 0) || (v >= 1 && v <= 99) || (v >= 100 && v <= 1000 && v % 100 == 0);
            if (!legal)
            {
                allLegal = false;
                bad += s + " ";
            }
        }
        T.Check("rri/every value legal (0, 1..99, 100..1000 step 100)", allLegal,
                Opt().Exp("all legal").Obs(allLegal ? "all legal" : "illegal: " + bad).Ref("TS 38.331 sl-ResourceReservePeriod"));
        T.Check("rri/list length <= 16", rriStudy.size() <= 16,
                Opt().Exp("<= 16").Obs(ptest::FmtI(static_cast<long long>(rriStudy.size()))).Ref("TS 38.331 maxReservePeriod"));
        // the stack default list (nr_sl_stack.cc:382-390): 0, 10..100 step 10, 150..1000 step 50 without 250
        std::vector<int> def = {0};
        for (int r = 10; r <= 100; r += 10) def.push_back(r);
        for (int r = 150; r <= 1000; r += 50)
        {
            if (r != 250) def.push_back(r);
        }
        int illegal = 0;
        for (int v : def)
        {
            illegal += ((v == 0) || (v >= 1 && v <= 99) || (v >= 100 && v % 100 == 0)) ? 0 : 1;
        }
        T.Info("rri/stack default list (empty --rriList)",
               Opt().Obs(std::to_string(def.size()) + " values, " + std::to_string(illegal) +
                         " not RRC-legal (150, 350, ..., 950), more than 16 entries")
                   .Exp("RRC: <= 16 entries, values 1..99 or multiples of 100")
                   .Note("the study passes the 14-value --rriList, so it is RRC-compliant; the stack default is not. "
                         "Code builds 28 values, CLI help says 27 (README vs code, section 6.3)"));
    }

    // ---- 3. SCI-1A round trip -----------------------------------------------------------------------------------
    {
        long total = 0, ok = 0;
        std::vector<std::string> badRows;
        for (int nmax : {2, 3})
        {
            for (int g1 = 1; g1 <= 31; ++g1)
            {
                const int g2 = (g1 == 31) ? 30 : g1 + 1;
                NrSlSciF1aHeader h = MakeF1a(nmax, g1, g2);
                ++total;
                const bool rt = RoundTripF1a(h);
                ok += rt ? 1 : 0;
                if (!rt && badRows.size() < 5)
                {
                    badRows.push_back("N=" + std::to_string(nmax) + " g1=" + std::to_string(g1));
                }
            }
        }
        T.Check("sci1a/round trip N_MAX in {2,3}, gaps 1..31", ok == total,
                Opt().Exp(ptest::FmtI(total) + " round trips").Obs(ptest::FmtI(ok) + " ok").Ref("TS 38.212 8.3.1.1"));
        // sizes: 10 mandatory bytes (priority, mcs, format, period 2, subchannels 2, start, length, N_MAX) + 2 (N=2) / + 4 (N=3)
        T.Check("sci1a/serialized size 12 (N=2), 14 (N=3)",
                MakeF1a(2, 3, 0).GetSerializedSize() == 12 && MakeF1a(3, 3, 4).GetSerializedSize() == 14,
                Opt().Exp("12 / 14 bytes (byte-aligned container, not the 3GPP bit layout)").Obs(ptest::FmtI(MakeF1a(2, 3, 0).GetSerializedSize()) + " / " +
                                               ptest::FmtI(MakeF1a(3, 3, 4).GetSerializedSize())));
        // gaps >= 32: the header is one byte per gap and accepts them (the 32-slot window of TS 38.212 is not enforced)
        long accepted = 0, tried = 0;
        for (int g = 32; g <= 255; ++g)
        {
            NrSlSciF1aHeader h = MakeF1a(3, static_cast<uint8_t>(g), static_cast<uint8_t>(g == 255 ? 254 : g + 1));
            ++tried;
            accepted += RoundTripF1a(h) ? 1 : 0;
        }
        T.Info("sci1a/gap >= 32 slots accepted by the header (DV8)",
               Opt().Obs(ptest::FmtI(accepted) + " of " + ptest::FmtI(tried) + " gaps in 32..255 serialize and round-trip")
                   .Exp("3GPP: all N_MAX resources within a 32-slot window (gap <= 31)")
                   .Fnd("DV8")
                   .Note("the header does not enforce the 32-slot window; F04 checks that the gaps used are <= 31"));
    }

    // ---- 4. SCI-2A round trip, cast types ---------------------------------------------------------------------
    {
        long total = 0, ok = 0;
        for (int cast = 0; cast <= 3; ++cast)
        {
            for (int harq = 0; harq <= 15; ++harq)
            {
                NrSlSciF2aHeader h;
                h.SetHarqId(static_cast<uint8_t>(harq));
                h.SetNdi(harq & 1);
                h.SetRv(harq & 3);
                h.SetSrcId(7);
                h.SetDstId(254);
                h.SetHarqFeedbackIndicator(cast == 0 ? 0 : 1);
                h.SetCastType(static_cast<uint8_t>(cast));
                h.SetCsiReq(0);
                Ptr<Packet> p = Create<Packet>();
                p->AddHeader(h);
                NrSlSciF2aHeader d;
                p->PeekHeader(d);
                ++total;
                const bool same = d.GetHarqId() == harq && d.GetCastType() == cast &&
                                  d.GetNdi() == (harq & 1) && d.GetRv() == (harq & 3) && d.GetDstId() == 254 &&
                                  d.GetHarqFeedbackIndicator() == (cast == 0 ? 0 : 1);
                ok += same ? 1 : 0;
            }
        }
        T.Check("sci2a/round trip cast 0..3 x HARQ id 0..15", ok == total,
                Opt().Exp(ptest::FmtI(total)).Obs(ptest::FmtI(ok)).Ref("TS 38.212 8.4.1.1"));
        // HARQ id 16 masks to 0 (4-bit field)
        {
            NrSlSciF2aHeader h;
            h.SetHarqId(16);
            h.SetNdi(1);
            h.SetRv(0);
            h.SetSrcId(7);
            h.SetDstId(254);
            h.SetHarqFeedbackIndicator(1);
            h.SetCastType(1);
            h.SetCsiReq(0);
            Ptr<Packet> p = Create<Packet>();
            p->AddHeader(h);
            NrSlSciF2aHeader d;
            p->PeekHeader(d);
            T.Info("sci2a/HARQ id 16 on the air", Opt().Obs("decodes as HARQ id " + std::to_string(d.GetHarqId()))
                                                      .Exp("4-bit field (0..15): 16 masks to 0")
                                                      .Note("the study never allocates more than 16 HARQ processes (B05 checks ids stay in 0..15)"));
        }
        // cast-type numeric codes vs TS 38.212 Table 8.4.1.1-1: 00 broadcast, 01 groupcast ACK/NACK (Option 2), 10 unicast,
        // 11 groupcast NACK-only (Option 1)
        const bool hdr = NrSlSciF2aHeader::Broadcast == 0 && NrSlSciF2aHeader::Groupcast == 1 &&
                         NrSlSciF2aHeader::Unicast == 2 && NrSlSciF2aHeader::GroupcastOnlyNack == 3;
        const bool tft = static_cast<int>(SidelinkInfo::CastType::Broadcast) == 0 &&
                         static_cast<int>(SidelinkInfo::CastType::Groupcast) == 1 &&
                         static_cast<int>(SidelinkInfo::CastType::Unicast) == 2 &&
                         static_cast<int>(SidelinkInfo::CastType::GroupcastNegativeOnly) == 3;
        T.Check("sci2a/NrSlSciF2aHeader cast codes = TS 38.212 (0,1,2,3)", hdr,
                Opt().Exp("Broadcast=0 Groupcast=1 (ACK/NACK, Option 2) Unicast=2 GroupcastOnlyNack=3 (Option 1)")
                    .Obs(hdr ? "match" : "mismatch").Ref("TS 38.212 Table 8.4.1.1-1 [V]"));
        T.Check("sci2a/SidelinkInfo::CastType codes = TS 38.212 (0,1,2,3)", tft,
                Opt().Exp("Broadcast=0 Groupcast=1 Unicast=2 GroupcastNegativeOnly=3").Obs(tft ? "match" : "mismatch")
                    .Ref("TS 38.212 Table 8.4.1.1-1 [V]"));
    }

    // ---- 5. CoopInfo is free on the air (DV6) -----------------------------------------------------------------
    {
        NrSlSciF2aHeader h;
        h.SetHarqId(1);
        h.SetNdi(1);
        h.SetRv(0);
        h.SetSrcId(1);
        h.SetDstId(254);
        h.SetHarqFeedbackIndicator(1);
        h.SetCastType(1);
        h.SetCsiReq(0);
        const uint32_t plain = h.GetSerializedSize();
        NrSlSciF2aHeader::CoopInfo ci;
        ci.helperId = 5;
        ci.sbLength = 2;
        ci.mcs = 14;
        h.SetCoopInfo(ci);
        const uint32_t ser = h.GetSerializedSize();
        const uint32_t air = h.GetAirSize();
        T.Check("coop/stock SCI-2A size is 5 bytes", plain == 5, Opt().Exp("5").Obs(ptest::FmtI(plain)));
        T.Check("coop/GetAirSize() == 5 with CoopInfo set", air == 5, Opt().Exp("5").Obs(ptest::FmtI(air)));
        T.CheckXfail("coop/CoopInfo serialized size equals the air size (signalling is charged)", ser == air,
                     Opt().Exp("serialized == air (46 B of coop info charged to the PSSCH)")
                         .Obs("serialized " + std::to_string(ser) + " B, air " + std::to_string(air) + " B")
                         .Fnd("DV6")
                         .Sev("L")
                         .Note("helper signalling (helperId, relay slots, TB size, deadline) travels as " +
                               std::to_string(ser - air) + " extra bytes that are not charged on the air interface: "
                               "the cooperative signalling is free (DV6, README intent)"));
    }

    return T.Finish();
}
