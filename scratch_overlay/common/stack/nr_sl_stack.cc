// nr_sl_stack.cc
//
// Derived from scratch_overlay/groupcast_eval/nr_sl_stack.cc with three
// changes:
//   1. BandwidthPartInfo::Scenario is a parameter (default V2V_Urban).
//   2. ApplyGeometricNlosv() is called immediately after InitializeOperationBand().
//   3. An RNTI→nodeId map is built for optional future use.
//   4. Explicit bandwidth control via channelBwMHz + numSubchannels.
//   5. MCD feedback gate integration (RegisterRntiMobility + InstallMcdFeedbackGate).

#include "nr_sl_stack.h"

#include "common/mcd/mcr_feedback_gate.h"
#include "common/psfch_kpi/psfch_kpi.h"
#include "common/stack/nr-sl-adaptive-mcs-scheduler.h"
#include "common/stack/nr-sl-deterministic-scheduler.h"

#include "ns3/antenna-module.h"
#include "ns3/buildings-helper.h"
#include "ns3/config.h"
#include "ns3/ipv4-static-routing-helper.h"
#include "ns3/log.h"
#include "ns3/lte-ue-rrc.h"
#include "ns3/node-list.h"
#include "ns3/nr-amc.h"
#include "ns3/pointer.h"

#include <cmath>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("CsiGroupcastEval.NrSlStack");

SlStackCtx
BuildSlStack(const SlStackParams& p,
             const GeomChannelCfg& geom,
             const NodeContainer& nodes)
{
    SlStackCtx ctx;

    // -----------------------------------------------------------------------
    // 1. Band / BWP: 5.89 GHz (band n47).
    //    Bandwidth: legacy = 200<<mu (100-kHz units); override via channelBwMHz.
    //    At mu=2 and 20 MHz: 20e6 / (15000 * 2^mu * 12) ≈ 51 PRBs.
    //    The NR BWP bandwidth is specified in units of 100 kHz.
    // -----------------------------------------------------------------------
    const double   centralFreqHz  = 5.89e9;

    // Compute fullBand100kHz:
    //   Legacy: 200 * 2^mu  (gives 80 MHz at mu=2).
    //   Override: round(channelBwMHz * 10) 100-kHz units.
    uint16_t fullBand100kHz;
    if (p.channelBwMHz > 0.0)
    {
        fullBand100kHz = static_cast<uint16_t>(std::round(p.channelBwMHz * 10.0));
        NS_LOG_INFO("NrSlStack: explicit bandwidth " << p.channelBwMHz
                    << " MHz → " << fullBand100kHz << " * 100 kHz units");
    }
    else
    {
        fullBand100kHz = static_cast<uint16_t>(200u << p.mu);
    }

    // Compute per-subchannel PRB count for the pool.
    // PRBs available ≈ floor(fullBand100kHz * 100e3 / (subcarriers_per_RB * subcarrier_spacing))
    //   subcarriers_per_RB = 12
    //   subcarrier_spacing = 15000 * 2^mu  Hz
    // Equivalently: availPRBs = floor(fullBand100kHz * 100e3 / (12 * 15000 * 2^mu))
    const double subcarrierSpacingHz = 15000.0 * (1u << p.mu);
    const double availPRBs = std::floor(
        (static_cast<double>(fullBand100kHz) * 100e3) / (12.0 * subcarrierSpacingHz));
    const uint32_t availPRBsInt = static_cast<uint32_t>(availPRBs);

    uint32_t subchSize;
    if (p.numSubchannels > 0 && p.channelBwMHz > 0.0)
    {
        // Snap subchSize to nearest valid value {10,15,20,25,50,75,100}.
        // Derived from availPRBs / numSubchannels, rounded up to next valid.
        const uint32_t raw = (availPRBsInt > 0 && p.numSubchannels > 0)
                             ? availPRBsInt / p.numSubchannels : 10;
        static const uint32_t valid[] = {10, 15, 20, 25, 50, 75, 100};
        subchSize = valid[sizeof(valid)/sizeof(valid[0]) - 1]; // default max
        for (uint32_t v : valid)
        {
            if (v >= raw) { subchSize = v; break; }
        }
        // Guard: if subchSize * numSubchannels > availPRBs, fall back to bwPRBs.
        if (subchSize * p.numSubchannels > availPRBsInt + 20 /* 20 PRB guard */)
        {
            NS_LOG_WARN("NrSlStack: computed subchSize=" << subchSize
                        << " × numSubchannels=" << p.numSubchannels
                        << " > availPRBs=" << availPRBsInt
                        << "; falling back to bwPRBs=" << p.bwPRBs);
            subchSize = p.bwPRBs;
        }
        NS_LOG_INFO("NrSlStack: " << p.numSubchannels << " subchannels"
                    << " × " << subchSize << " PRBs/subch"
                    << " (avail=" << availPRBsInt << " PRBs, raw=" << raw << ")");
    }
    else
    {
        subchSize = p.bwPRBs; // legacy
    }

    CcBwpCreator::SimpleOperationBandConf bandConf(centralFreqHz,
                                                    fullBand100kHz,
                                                    1 /* numCcPerBand */,
                                                    p.scenario);
    CcBwpCreator ccBwpCreator;
    OperationBandInfo bandSl = ccBwpCreator.CreateOperationBandContiguousCc(bandConf);

    // -----------------------------------------------------------------------
    // 2. NrHelper: channel condition update period + shadowing.
    // -----------------------------------------------------------------------
    ctx.epc = CreateObject<NrPointToPointEpcHelper>();
    ctx.nr  = CreateObject<NrHelper>();
    ctx.nr->SetEpcHelper(ctx.epc);
    ctx.epc->SetAttribute("S1uLinkDelay", TimeValue(MilliSeconds(0)));

    // ThreeGppChannelModel UpdatePeriod: sets how often the spatial-channel
    // matrix (small-scale fading coefficients) is recalculated.
    // 10 ms ≈ Tc/4 at 60 km/h / 5.9 GHz → tracks fast fading without
    // recomputing every slot.  Must be < coherence time (~4–40 ms for V2X).
    Config::SetDefault("ns3::ThreeGppChannelModel::UpdatePeriod",
                       TimeValue(MilliSeconds(10)));
    // Channel-condition model (LOS/NLOSv determination) update interval.
    ctx.nr->SetChannelConditionModelAttribute("UpdatePeriod",
                                              TimeValue(MilliSeconds(10)));
    ctx.nr->SetPathlossAttribute("ShadowingEnabled",
                                 BooleanValue(p.shadowingEnabled));

    // InitializeOperationBand creates the default CCM and wires it into
    // the propagation + spectrum models.  ApplyGeometricNlosv() replaces
    // those CCMs with the geometric wrapper immediately afterward.
    ctx.nr->InitializeOperationBand(&bandSl);

    // -----------------------------------------------------------------------
    // 3. Replace channel-condition model with geometric NLOSv wrapper.
    // -----------------------------------------------------------------------
    ApplyGeometricNlosv(bandSl, geom);

    BandwidthPartInfoPtrVector allBwps = CcBwpCreator::GetAllBwps({bandSl});

    Packet::EnablePrinting();

    // Capture the (geometric-wrapped) channel-condition model while allBwps is
    // still alive: GetAllBwps moves the BandwidthPartInfoPtrs out of the band,
    // so they die with this function. The CCM itself is refcounted and lives on.
    if (!allBwps.empty())
    {
        if (auto* bwp = allBwps[0].get().get())
        {
            if (auto pl = DynamicCast<ThreeGppPropagationLossModel>(bwp->m_propagation))
            {
                ctx.channelConditionModel = pl->GetChannelConditionModel();
            }
        }
    }

    // -----------------------------------------------------------------------
    // 4. UE antenna + PHY attributes.
    // -----------------------------------------------------------------------
    ctx.nr->SetUeAntennaAttribute("NumRows",    UintegerValue(1));
    ctx.nr->SetUeAntennaAttribute("NumColumns", UintegerValue(2));
    ctx.nr->SetUeAntennaAttribute("AntennaElement",
                                  PointerValue(CreateObject<IsotropicAntennaModel>()));
    ctx.nr->SetUePhyAttribute("TxPower", DoubleValue(23.0));

    ctx.nr->SetUeMacTypeId(NrSlUeMac::GetTypeId());
    ctx.nr->SetUeMacAttribute("EnableSensing",       BooleanValue(true));
    ctx.nr->SetUeMacAttribute("T1",                  UintegerValue(2));
    ctx.nr->SetUeMacAttribute("T2",                  UintegerValue(33));
    ctx.nr->SetUeMacAttribute("ActivePoolId",         UintegerValue(0));
    ctx.nr->SetUeMacAttribute("SlThresPsschRsrp",     IntegerValue(-128));

    const uint8_t bwpIdSl = 0;
    ctx.nr->SetBwpManagerTypeId(TypeId::LookupByName("ns3::NrSlBwpManagerUe"));
    ctx.nr->SetUeBwpManagerAlgorithmAttribute("GBR_MC_PUSH_TO_TALK",
                                              UintegerValue(bwpIdSl));
    std::set<uint8_t> bwpIdContainer{bwpIdSl};

    // -----------------------------------------------------------------------
    // 5. Install UE devices + assign streams.
    //    BuildingsHelper attaches MobilityBuildingInfo to every node so that
    //    the ThreeGppV2vUrbanChannelConditionModel (used by V2V_Urban) can
    //    call IsIndoor() without a null-pointer crash.
    // -----------------------------------------------------------------------
    BuildingsHelper::Install(nodes);
    ctx.ueNetDev = ctx.nr->InstallUeDevice(nodes, allBwps);
    for (auto it = ctx.ueNetDev.Begin(); it != ctx.ueNetDev.End(); ++it)
        DynamicCast<NrUeNetDevice>(*it)->UpdateConfig();

    ctx.stream = 10 * static_cast<int64_t>(p.seed) + 1;
    ctx.stream += ctx.nr->AssignStreams(ctx.ueNetDev, ctx.stream);

    // -----------------------------------------------------------------------
    // 6. NrSlHelper: NrEesmIrT1 error model + FixedMcs scheduler.
    // -----------------------------------------------------------------------
    ctx.sl = CreateObject<NrSlHelper>();
    ctx.sl->SetEpcHelper(ctx.epc);
    ctx.sl->SetSlErrorModel("ns3::NrEesmIrT1");
    ctx.sl->SetUeSlAmcAttribute("AmcModel", EnumValue(NrAmc::ErrorModel));
    // Set default scheduler for all nodes: NrSlUeMacSchedulerFixedMcs
    ctx.sl->SetNrSlSchedulerTypeId(NrSlUeMacSchedulerFixedMcs::GetTypeId());
    ctx.sl->SetUeSlSchedulerAttribute("Mcs", UintegerValue(p.mcs));
    ctx.sl->PrepareUeForSidelink(ctx.ueNetDev, bwpIdContainer);
    ctx.stream += ctx.sl->AssignStreams(ctx.ueNetDev, ctx.stream);

    // -----------------------------------------------------------------------
    // 6.0. (Optional) Per-node MCS override.
    // -----------------------------------------------------------------------
    // Backward-compat: empty map = no-op, every node keeps the global MCS.
    // When non-empty, for each (nodeId, mcs) entry replace the scheduler on the
    // listed TX node with a fresh NrSlUeMacSchedulerFixedMcs at the per-node MCS.
    // RX-only nodes are unaffected (they have no PSSCH-TX role). Used by
    // relay_mcs_resource_eval to give leader (node 0) and relay (node r)
    // independent MCS values for the 2-hop same-L2ID relay.
    if (!p.nodeMcsOverride.empty())
    {
        for (const auto& kv : p.nodeMcsOverride)
        {
            const uint32_t nodeId = kv.first;
            const uint8_t  nodeMcs = kv.second;
            Ptr<Node> targetNode = NodeList::GetNode(nodeId);
            if (!targetNode || targetNode->GetNDevices() == 0) continue;
            auto dev = targetNode->GetDevice(0)->GetObject<NrUeNetDevice>();
            if (!dev) continue;
            auto nrSlMac = dev->GetMac(0)->GetObject<NrSlUeMac>();
            if (!nrSlMac) continue;
            Ptr<NrSlUeMacSchedulerFixedMcs> perNodeSched =
                CreateObject<NrSlUeMacSchedulerFixedMcs>();
            perNodeSched->SetAttribute("Mcs", UintegerValue(nodeMcs));
            Ptr<NrAmc> perNodeAmc = CreateObject<NrAmc>();
            perNodeAmc->SetAttribute("AmcModel", EnumValue(NrAmc::ErrorModel));
            perNodeSched->SetNrSlUeMac(nrSlMac);
            perNodeSched->InstallAmc(perNodeAmc);
            nrSlMac->SetAttribute("NrSlUeMacScheduler", PointerValue(perNodeSched));
            NS_LOG_INFO("NrSlStack: nodeId=" << nodeId
                        << " MCS overridden to " << +nodeMcs);
        }
    }

    // -----------------------------------------------------------------------
    // 6a. (Optional) Deterministic collision-free scheduler on every UE.
    // -----------------------------------------------------------------------
    // Replaces SB-SPS with NrSlDeterministicScheduler so that node 0 (leader)
    // and the relay candidates transmit on disjoint partitions and never
    // collide (p_col -> 0). Node 0 gets lane 0; every other UE gets lane 1.
    //
    // DISABLED: we tested this for Mikami et al. 2020 and it makes things
    // WORSE, not better. Partitions silo UEs onto disjoint subchannels, so
    // each UE's sensing window only sees TXs in its own lane — random slot
    // selections across lanes then collide in the TIME domain with no
    // recovery (the lost TBs aren't retransmittable because retx slots hit
    // the same half-duplex conflict). See VALIDATION_REPORT.md §7.
    // Default remains plain SB-SPS (paper-faithful).
    const bool deterministic = (p.schedulerMode == "deterministic");
    if (deterministic)
    {
        const bool tdm = (p.detScheme == "tdm");
        for (uint32_t i = 0; i < ctx.ueNetDev.GetN(); ++i)
        {
            auto dev = ctx.ueNetDev.Get(i)->GetObject<NrUeNetDevice>();
            if (!dev)
                continue;
            auto nrSlMac = dev->GetMac(0)->GetObject<NrSlUeMac>();
            if (!nrSlMac)
                continue;

            Ptr<NrSlDeterministicScheduler> det = CreateObject<NrSlDeterministicScheduler>();
            det->SetNrSlUeMac(nrSlMac);
            Ptr<NrAmc> slAmc = CreateObject<NrAmc>();
            slAmc->SetAttribute("AmcModel", EnumValue(NrAmc::ErrorModel));
            det->InstallAmc(slAmc);
            det->SetAttribute("Mcs", UintegerValue(p.mcs));

            // Combined FDM+TDM partition: each UE gets its own
            // (subchannel, slot-phase) cell so disjoint reservations are
            // guaranteed when numUEs > detNumLanes (i.e., > numSubchannels).
            //
            // For 20 MHz / 2 subchannels / 3 UEs: FDM gives only 2 disjoint
            // lanes; adding TDM modulus=numSubchannels, phase=i tiles the 3rd
            // UE into the unused (slot, subch) cell.
            uint8_t laneIdx = (p.detNumLanes > 0) ? (i % p.detNumLanes) : 0;
            NS_LOG_INFO("Deterministic scheduler UE i=" << i << " device=" << dev
                                                       << " RNTI=" << nrSlMac->GetRnti()
                                                       << " → lane " << +laneIdx
                                                       << " (modulus=" << +p.detNumLanes << ")");
            // TDM-only partition: each UE gets a unique slot phase so the
            // 3 (or N) UEs transmit on disjoint slots. CTTC's scheduler
            // always grants the full BW (subchLen = numSubchannels) per TX,
            // so subchannel-level FDM filtering is not effective — TDM
            // (slot phase) is the only way to guarantee disjoint reservations
            // when each UE must use the full bandwidth.
            //
            // For 3 UEs at RRI=10 ms = 40 slots, modulus=3, phase=i:
            //   UE0 → slot 0, 3, 6, …  (slot % 3 == 0)
            //   UE1 → slot 1, 4, 7, …  (slot % 3 == 1)
            //   UE2 → slot 2, 5, 8, …  (slot % 3 == 2)
            // Each UE picks the first matching slot → exactly 1 reservation
            // per RRI cycle, in distinct slots → 0 collisions.
            const uint32_t numUes = ctx.ueNetDev.GetN();
            det->SetSlotPartition(numUes, i);
            // Carry per-destination fixed-MCS overrides (split-MCS groupcast).
            for (const auto& kv : p.dstMcsOverride)
                det->SetDstMcsOverride(kv.first, kv.second);

            nrSlMac->SetAttribute("NrSlUeMacScheduler", PointerValue(det));
        }
        NS_LOG_INFO("Installed NrSlDeterministicScheduler (" << p.detScheme
                    << ", " << +p.detNumLanes << " lanes) on all UEs");
    }

    // -----------------------------------------------------------------------
    // 6b. (Optional) Replace scheduler on TX node (node 0) with adaptive scheduler.
    // -----------------------------------------------------------------------
    // Skipped when p.adaptiveMcs == false so that fixed-MCS sweeps honour --mcs.
    // ALSO entered (independent of adaptiveMcs) when per-destination fixed-MCS
    // overrides are requested: the adaptive scheduler is the only one exposing
    // SetDstMcsOverride(), but the override pins MCS so no adaptation occurs.
    if (!deterministic && (p.adaptiveMcs || !p.dstMcsOverride.empty()))
    {
    Ptr<Node> txNode = nodes.Get(0);
    auto txDev = txNode->GetDevice(0)->GetObject<NrUeNetDevice>();
    if (txDev)
    {
        auto nrSlMac = txDev->GetMac(0)->GetObject<NrSlUeMac>();
        if (nrSlMac)
        {
            // Create adaptive scheduler instance
            Ptr<NrSlAdaptiveMcsScheduler> adaptSched = CreateObject<NrSlAdaptiveMcsScheduler>();
            adaptSched->SetNrSlUeMac(nrSlMac);  // Wire m_ueMac so GetMacHarq() works
            Ptr<NrAmc> slAmc = CreateObject<NrAmc>();
            slAmc->SetAttribute("AmcModel", EnumValue(NrAmc::ErrorModel));
            adaptSched->InstallAmc(slAmc); // Wire AMC so CalculateTbSize() works
            adaptSched->SetAttribute("Mcs", UintegerValue(p.mcs));
            // Pin per-destination fixed MCS (dual-MCS redundant groupcast).
            // SetDstMcsOverride() takes highest priority and bypasses adaptation,
            // so each listed dstL2Id transmits at its fixed MCS forever.
            for (const auto& kv : p.dstMcsOverride)
            {
                adaptSched->SetDstMcsOverride(kv.first, kv.second);
                NS_LOG_INFO("Pinned dstL2Id=" << kv.first
                            << " to fixed MCS=" << +kv.second);
            }
            // Replace the scheduler in the MAC
            nrSlMac->SetAttribute("NrSlUeMacScheduler", PointerValue(adaptSched));
            NS_LOG_INFO("Replaced scheduler on TX node 0 with NrSlAdaptiveMcsScheduler");
        }
    }
    } // end if (p.adaptiveMcs || overrides)

    // -----------------------------------------------------------------------
    // 7. Resource pool: slBitmap "111111000111" (9/12 subframes for SL).
    // -----------------------------------------------------------------------
    Ptr<NrSlCommResourcePoolFactory> poolFactory =
        Create<NrSlCommResourcePoolFactory>();
    // Dedicated carrier (p.dedicatedCarrier): all-ones bitmap on an all-UL
    // TDD pattern, i.e. every physical slot is a sidelink slot.
    std::vector<std::bitset<1>> slBitmap = {1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1};
    if (p.dedicatedCarrier)
    {
        slBitmap.assign(10, std::bitset<1>(1));
    }
    poolFactory->SetSlTimeResources(slBitmap);
    poolFactory->SetSlSensingWindow(100);
    poolFactory->SetSlSelectionWindow(5);
    poolFactory->SetSlFreqResourcePscch(10);
    poolFactory->SetSlSubchannelSize(subchSize);
    // Default 1 = disable blind retx reservation; retx is NACK-driven.
    // Set to 3 to restore the mode-2 spec default (initial + 2 blind retx slots
    // pre-reserved in SCI-1A). See SlStackParams::maxNumPerReserve for details.
    poolFactory->SetSlMaxNumPerReserve(p.maxNumPerReserve);
    // Build the allowed-RRI list.
    // The ns-3 NrSlCommResourcePoolFactory switch only recognises:
    //   0 (DG sentinel)
    //   10, 20, ..., 100 ms  (multiples of 10)
    //   150, 200, ..., 1000 ms  (multiples of 50)
    // Any other value triggers NS_FATAL_ERROR at runtime.
    // Default = advertise the complete supported set so every flow RRI is valid.
    // Callers may supply a smaller override via SlStackParams::rriList.
    std::list<uint16_t> rriList;
    if (!p.rriList.empty())
    {
        rriList = p.rriList;
    }
    else
    {
        rriList.push_back(0);            // dynamic-grant sentinel
        for (uint16_t r = 10; r <= 100; r += 10)
            rriList.push_back(r);        // 10, 20, ..., 100
        for (uint16_t r = 150; r <= 1000; r += 50)
        {
            if (r == 250) continue;      // LteRrcSap::GetSlResoResvPrdValue missing MS250
            rriList.push_back(r);        // 150, 200, 300, ..., 1000
        }
    }
    poolFactory->SetSlResourceReservePeriodList(rriList);
    poolFactory->SetSlPsfchPeriod(p.psfchPeriod);
    poolFactory->SetSlMinTimeGapPsfch(3);

    LteRrcSap::SlResourcePoolNr slPool = poolFactory->CreatePool();

    LteRrcSap::SlResourcePoolIdNr poolId;
    poolId.id = 0;
    LteRrcSap::SlResourcePoolConfigNr poolCfg;
    poolCfg.haveSlResourcePoolConfigNr = true;
    poolCfg.slResourcePoolId           = poolId;
    poolCfg.slResourcePool             = slPool;

    LteRrcSap::SlBwpPoolConfigCommonNr bwpPoolCfg;
    bwpPoolCfg.slTxPoolSelectedNormal[poolId.id] = poolCfg;

    LteRrcSap::Bwp bwp;
    bwp.numerology       = p.mu;
    bwp.symbolsPerSlots  = 14;
    bwp.rbPerRbg         = 1;
    bwp.bandwidth        = fullBand100kHz;

    LteRrcSap::SlBwpGeneric slBwpGeneric;
    slBwpGeneric.bwp              = bwp;
    slBwpGeneric.slLengthSymbols  = LteRrcSap::GetSlLengthSymbolsEnum(14);
    slBwpGeneric.slStartSymbol    = LteRrcSap::GetSlStartSymbolEnum(0);

    LteRrcSap::SlBwpConfigCommonNr slBwpCfg;
    slBwpCfg.haveSlBwpGeneric            = true;
    slBwpCfg.slBwpGeneric                = slBwpGeneric;
    slBwpCfg.haveSlBwpPoolConfigCommonNr = true;
    slBwpCfg.slBwpPoolConfigCommonNr     = bwpPoolCfg;

    LteRrcSap::SlFreqConfigCommonNr slFreqCfg;
    for (const auto& bid : bwpIdContainer)
        slFreqCfg.slBwpList[bid] = slBwpCfg;

    LteRrcSap::TddUlDlConfigCommon tddCfg;
    tddCfg.tddPattern = p.dedicatedCarrier ? "UL|UL|UL|UL|UL|UL|UL|UL|UL|UL|"
                                           : "DL|DL|DL|F|UL|UL|UL|UL|UL|UL|";

    LteRrcSap::SlPreconfigGeneralNr slPreGen;
    slPreGen.slTddConfig = tddCfg;

    LteRrcSap::SlPsschTxParameters psschParams;
    psschParams.slMaxTxTransNumPssch = p.maxNumTx;
    LteRrcSap::SlPsschTxConfigList psschTxList;
    psschTxList.slPsschTxParameters[0] = psschParams;

    LteRrcSap::SlUeSelectedConfig slUeSel;
    slUeSel.slProbResourceKeep = p.probResourceKeep;
    slUeSel.slPsschTxConfigList = psschTxList;

    LteRrcSap::SidelinkPreconfigNr slPreConfig;
    slPreConfig.slPreconfigGeneral         = slPreGen;
    slPreConfig.slUeSelectedPreConfig      = slUeSel;
    slPreConfig.slPreconfigFreqInfoList[0] = slFreqCfg;

    ctx.sl->InstallNrSlPreConfiguration(ctx.ueNetDev, slPreConfig);

    // -----------------------------------------------------------------------
    // 8. IP stack + default routes.
    // -----------------------------------------------------------------------
    InternetStackHelper internet;
    internet.Install(nodes);
    ctx.stream += internet.AssignStreams(nodes, ctx.stream);
    ctx.ueIpIface = ctx.epc->AssignUeIpv4Address(ctx.ueNetDev);

    Ipv4StaticRoutingHelper ipv4RoutingHelper;
    for (uint32_t u = 0; u < nodes.GetN(); ++u)
    {
        Ptr<Ipv4StaticRouting> sr =
            ipv4RoutingHelper.GetStaticRouting(nodes.Get(u)->GetObject<Ipv4>());
        sr->SetDefaultRoute(ctx.epc->GetUeDefaultGatewayAddress(), 1);
    }

    // -----------------------------------------------------------------------
    // 9. Build RNTI→nodeId map (cheap; useful for future CSI attribution).
    // -----------------------------------------------------------------------
    for (uint32_t i = 0; i < ctx.ueNetDev.GetN(); ++i)
    {
        auto dev = DynamicCast<NrUeNetDevice>(ctx.ueNetDev.Get(i));
        if (!dev) continue;
        auto rrc = dev->GetRrc();
        if (!rrc) continue;
        uint16_t rnti   = rrc->GetRnti();
        uint32_t nodeId = nodes.Get(i)->GetId();
        ctx.rntiToNode[rnti] = nodeId;
    }

    // -----------------------------------------------------------------------
    // 10. MCR feedback gate (HARQ Option-1 only — spec-faithful).
    //
    //     Per 3GPP TS 38.321 §5.4.4.1.1, the receiver-side gating of PSFCH
    //     feedback depends on the HARQ feedback option:
    //
    //     * HARQ Option 1 (GroupcastOnlyNack): receivers send NACK only when
    //       they fail to decode AND are within MCR of the transmitter. The
    //       MCR distance gate IS the spec mechanism for Option-1.
    //
    //     * HARQ Option 2 (Groupcast): receivers send ACK/NACK gated by
    //       Zone-ID membership (sl-ZoneConfig in RRC), NOT by distance. We do
    //       not implement Zone-ID filtering; in our single-dstL2Id deployment
    //       all UEs are group members, so the spec-faithful behavior is to
    //       let all feedback flow without any distance filter.
    //
    //     InstallMcrFeedbackGate() implements the Option-1 MCR gate. We
    //     install it only when harqOption == 1. For harqOption == 2, no
    //     distance gate is installed regardless of mcrMeters.
    //
    //     RegisterRntiMobility() is SIMULATOR-SCHEDULED here because the
    //     NR RRC assigns RNTIs via asynchronous events that run during
    //     Simulator::Run() (e.g. StartContentionResolution scheduled from
    //     RecvRnti).  Calling it synchronously before Run() finds all RNTIs
    //     still equal to 0, leaving the registry empty and defeating the gate.
    //     The poll interval (1 ms) is well below the RACH response time so
    //     all UEs will have valid RNTIs by the time the sidelink actually
    //     starts transmitting at ~100 ms.
    //
    //     Note: contrib/nr does not implement the actual PSFCH freq×time×code
    //     multiplexing defined by 3GPP — each RX UE's feedback is delivered to
    //     the sender's MAC as a discrete callback carrying nodeId. This
    //     sidesteps the spec-defined PSFCH capacity limit and is acceptable
    //     for small-N sweeps (≤20 RX per TB); for large-N, capacity-bias
    //     would surface.
    // -----------------------------------------------------------------------
    if (p.harqOption == 1 && p.mcrMeters > 0.0)
    {
        InstallMcrFeedbackGate(ctx.ueNetDev, p.mcrMeters);
        Simulator::Schedule(MilliSeconds(0),
                            &RegisterRntiMobilityPoll,
                            ctx.ueNetDev);
        NS_LOG_INFO("NrSlStack: MCR feedback gate installed (Option-1), mcr="
                    << p.mcrMeters << " m");
    }
    else if (p.harqOption == 2 && p.mcrMeters > 0.0)
    {
        NS_LOG_WARN("NrSlStack: harqOption=2 (Groupcast ACK/NACK) — MCR distance"
                    << " gate NOT installed (spec mechanism is Zone-ID, not distance)."
                    << " mcrMeters=" << p.mcrMeters << " m is ignored.");
    }

    // -----------------------------------------------------------------------
    // PSFCH feedback KPI (HARQ Option-2 / Unicast measurement)
    //
    // Installs PsfchKpiShim on every UE spectrum phy's
    // SetNrPhyRxSlPsfchCallback. Records ACK/NACK arrivals per
    // (sender, dstL2Id, harqPid, rx) and emits SILENCE rows when a PSFCH
    // opportunity closes without feedback from an expected group member.
    //
    // CastType filtering (in PsfchKpiCollector::RecordArrival):
    //   - Broadcast dstL2Ids are skipped (spec has no PSFCH feedback).
    //   - Option-1 NACK-only is OUT OF SCOPE for v1.
    //   - Option-2 Groupcast and Unicast are recorded.
    //
    // For silence to work, groupcast_flows.cc must register dstL2Id members
    // via PsfchKpiCollector::Instance().RegisterDstL2IdMembers(). That
    // happens during InstallGroupcastFlows().
    // -----------------------------------------------------------------------
    if (p.recordPsfchKpi)
    {
        const bool mcrInstalled = (p.harqOption == 1 && p.mcrMeters > 0.0);
        InstallPsfchKpiArrivals(ctx.ueNetDev, mcrInstalled);
        PsfchKpiCollector::Instance().ScheduleCsvWrite(p.psfchKpiPath,
                                                       p.psfchKpiSummaryOnly);
        NS_LOG_INFO("NrSlStack: PSFCH KPI installed (post-gate) — path='"
                    << (p.psfchKpiPath.empty() ? "<disabled>" : p.psfchKpiPath)
                    << "' summaryOnly=" << (p.psfchKpiSummaryOnly ? "true" : "false"));
    }

    NS_LOG_INFO("NR-SL stack built: scenario=" << p.scenario
                << " mu=" << p.mu
                << " bwPRBs=" << p.bwPRBs
                << " mcs=" << p.mcs
                << " geom_blockers=" << geom.blockerSumoIds.size());
    return ctx;
}

} // namespace ns3
