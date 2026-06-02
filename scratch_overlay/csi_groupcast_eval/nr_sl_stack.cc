// nr_sl_stack.cc
//
// Derived from scratch_overlay/groupcast_eval/nr_sl_stack.cc with three
// changes:
//   1. BandwidthPartInfo::Scenario is a parameter (default V2V_Urban).
//   2. ApplyGeometricNlosv() is called immediately after InitializeOperationBand().
//   3. An RNTI→nodeId map is built for optional future use.

#include "nr_sl_stack.h"

#include "nr-sl-adaptive-mcs-scheduler.h"

#include "ns3/antenna-module.h"
#include "ns3/buildings-helper.h"
#include "ns3/config.h"
#include "ns3/ipv4-static-routing-helper.h"
#include "ns3/log.h"
#include "ns3/lte-ue-rrc.h"
#include "ns3/nr-amc.h"
#include "ns3/pointer.h"

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
    //    Bandwidth scales with numerology so PRB count stays ~106 across mu.
    // -----------------------------------------------------------------------
    const double   centralFreqHz  = 5.89e9;
    const uint16_t fullBand100kHz = static_cast<uint16_t>(200u << p.mu);

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
    // 6b. (Optional) Replace scheduler on TX node (node 0) with adaptive scheduler.
    // -----------------------------------------------------------------------
    // Skipped when p.adaptiveMcs == false so that fixed-MCS sweeps honour --mcs.
    if (p.adaptiveMcs)
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
            // Replace the scheduler in the MAC
            nrSlMac->SetAttribute("NrSlUeMacScheduler", PointerValue(adaptSched));
            NS_LOG_INFO("Replaced scheduler on TX node 0 with NrSlAdaptiveMcsScheduler");
        }
    }
    } // end if (p.adaptiveMcs)

    // -----------------------------------------------------------------------
    // 7. Resource pool: slBitmap "111111000111" (9/12 subframes for SL).
    // -----------------------------------------------------------------------
    Ptr<NrSlCommResourcePoolFactory> poolFactory =
        Create<NrSlCommResourcePoolFactory>();
    std::vector<std::bitset<1>> slBitmap = {1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1};
    poolFactory->SetSlTimeResources(slBitmap);
    poolFactory->SetSlSensingWindow(100);
    poolFactory->SetSlSelectionWindow(5);
    poolFactory->SetSlFreqResourcePscch(10);
    poolFactory->SetSlSubchannelSize(p.bwPRBs);
    poolFactory->SetSlMaxNumPerReserve(3);
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
    tddCfg.tddPattern = "DL|DL|DL|F|UL|UL|UL|UL|UL|UL|";

    LteRrcSap::SlPreconfigGeneralNr slPreGen;
    slPreGen.slTddConfig = tddCfg;

    LteRrcSap::SlPsschTxParameters psschParams;
    psschParams.slMaxTxTransNumPssch = p.maxNumTx;
    LteRrcSap::SlPsschTxConfigList psschTxList;
    psschTxList.slPsschTxParameters[0] = psschParams;

    LteRrcSap::SlUeSelectedConfig slUeSel;
    slUeSel.slProbResourceKeep = 0.0;
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

    NS_LOG_INFO("NR-SL stack built: scenario=" << p.scenario
                << " mu=" << p.mu
                << " bwPRBs=" << p.bwPRBs
                << " mcs=" << p.mcs
                << " geom_blockers=" << geom.blockerSumoIds.size());
    return ctx;
}

} // namespace ns3
