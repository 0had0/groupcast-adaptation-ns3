// nr_sl_stack.cc

#include "nr_sl_stack.h"

#include "ns3/antenna-module.h"
#include "ns3/config.h"
#include "ns3/ipv4-static-routing-helper.h"
#include "ns3/log.h"
#include "ns3/pointer.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("GroupcastEval.NrSlStack");

SlStackCtx
BuildSlStack(const SlStackParams& p, const NodeContainer& nodes)
{
    SlStackCtx ctx;

    // -----------------------------------------------------------------------
    // 1. Band / BWP: 5.89 GHz (band n47), 20 MHz, V2V_Highway channel model.
    //    bwPRBs is the *subchannel* size used by the resource pool; the
    //    physical bandwidth is always the full 20 MHz = 200 × 100 kHz.
    //    Pattern: sl-multi-lc-example.cc:372-400
    // -----------------------------------------------------------------------
    const double centralFreqHz = 5.89e9;
    // Bandwidth scales with numerology: SCS doubles per mu step, so PRB count
    // stays ~106 for mu=0/1/2 → supports bwPRBs up to 100 across all sweep points.
    //   mu=0 (15 kHz SCS): 200 × 100 kHz = 20 MHz → ~106 PRBs
    //   mu=1 (30 kHz SCS): 400 × 100 kHz = 40 MHz → ~106 PRBs
    //   mu=2 (60 kHz SCS): 800 × 100 kHz = 80 MHz → ~107 PRBs
    const uint16_t fullBand100kHz = static_cast<uint16_t>(200u << p.mu);

    CcBwpCreator::SimpleOperationBandConf bandConf(centralFreqHz,
                                                   fullBand100kHz,
                                                   1 /* numCcPerBand */,
                                                   BandwidthPartInfo::V2V_Highway);
    CcBwpCreator ccBwpCreator;
    OperationBandInfo bandSl = ccBwpCreator.CreateOperationBandContiguousCc(bandConf);

    // -----------------------------------------------------------------------
    // 2. NrHelper: channel model (static, no shadowing) + UE PHY/MAC attrs.
    //    Pattern: sl-multi-lc-example.cc:401-445
    // -----------------------------------------------------------------------
    ctx.epc = CreateObject<NrPointToPointEpcHelper>();
    ctx.nr = CreateObject<NrHelper>();
    ctx.nr->SetEpcHelper(ctx.epc);
    ctx.epc->SetAttribute("S1uLinkDelay", TimeValue(MilliSeconds(0)));

    Config::SetDefault("ns3::ThreeGppChannelModel::UpdatePeriod", TimeValue(MilliSeconds(1)));
    ctx.nr->SetChannelConditionModelAttribute("UpdatePeriod", TimeValue(MilliSeconds(1)));
    ctx.nr->SetPathlossAttribute("ShadowingEnabled", BooleanValue(false));
    ctx.nr->InitializeOperationBand(&bandSl);
    BandwidthPartInfoPtrVector allBwps = CcBwpCreator::GetAllBwps({bandSl});

    Packet::EnableChecking();
    Packet::EnablePrinting();

    // Quasi-omnidirectional 1×2 antenna (SISO, isotropic element).
    ctx.nr->SetUeAntennaAttribute("NumRows", UintegerValue(1));
    ctx.nr->SetUeAntennaAttribute("NumColumns", UintegerValue(2));
    ctx.nr->SetUeAntennaAttribute("AntennaElement",
                                  PointerValue(CreateObject<IsotropicAntennaModel>()));
    ctx.nr->SetUePhyAttribute("TxPower", DoubleValue(23.0)); // dBm

    // NrSlUeMac: sensing enabled (Mode-2), selection window T1=2/T2=33.
    ctx.nr->SetUeMacTypeId(NrSlUeMac::GetTypeId());
    ctx.nr->SetUeMacAttribute("EnableSensing", BooleanValue(true));
    ctx.nr->SetUeMacAttribute("T1", UintegerValue(2));
    ctx.nr->SetUeMacAttribute("T2", UintegerValue(33));
    ctx.nr->SetUeMacAttribute("ActivePoolId", UintegerValue(0));
    ctx.nr->SetUeMacAttribute("SlThresPsschRsrp", IntegerValue(-128));

    // BWP manager: map GBR_MC_PUSH_TO_TALK (used internally by NR SL RRC) to BWP 0.
    const uint8_t bwpIdSl = 0;
    ctx.nr->SetBwpManagerTypeId(TypeId::LookupByName("ns3::NrSlBwpManagerUe"));
    ctx.nr->SetUeBwpManagerAlgorithmAttribute("GBR_MC_PUSH_TO_TALK", UintegerValue(bwpIdSl));
    std::set<uint8_t> bwpIdContainer{bwpIdSl};

    // -----------------------------------------------------------------------
    // 3. Install UE devices and assign random streams.
    // -----------------------------------------------------------------------
    ctx.ueNetDev = ctx.nr->InstallUeDevice(nodes, allBwps);
    for (auto it = ctx.ueNetDev.Begin(); it != ctx.ueNetDev.End(); ++it)
    {
        DynamicCast<NrUeNetDevice>(*it)->UpdateConfig();
    }

    ctx.stream = 10 * static_cast<int64_t>(p.seed) + 1;
    ctx.stream += ctx.nr->AssignStreams(ctx.ueNetDev, ctx.stream);

    // -----------------------------------------------------------------------
    // 4. NrSlHelper: error model (IR-T1), fixed-MCS scheduler.
    //    Pattern: sl-multi-lc-example.cc:435-445
    // -----------------------------------------------------------------------
    ctx.sl = CreateObject<NrSlHelper>();
    ctx.sl->SetEpcHelper(ctx.epc);
    ctx.sl->SetSlErrorModel("ns3::NrEesmIrT1");
    ctx.sl->SetUeSlAmcAttribute("AmcModel", EnumValue(NrAmc::ErrorModel));
    ctx.sl->SetNrSlSchedulerTypeId(NrSlUeMacSchedulerFixedMcs::GetTypeId());
    ctx.sl->SetUeSlSchedulerAttribute("Mcs", UintegerValue(p.mcs));
    ctx.sl->PrepareUeForSidelink(ctx.ueNetDev, bwpIdContainer);
    ctx.stream += ctx.sl->AssignStreams(ctx.ueNetDev, ctx.stream);

    // -----------------------------------------------------------------------
    // 5. Resource pool + SidelinkPreconfigNr.
    //    Pattern: sl-multi-lc-example.cc:450-555
    //
    //    slBitmap "1|1|1|1|1|1|0|0|0|1|1|1" — 9 out of 12 subframes available
    //    for SL, matching the standard V2V highway configuration.
    //    The RRI list {0,20,50,100} covers all three flows' reservation periods.
    // -----------------------------------------------------------------------
    Ptr<NrSlCommResourcePoolFactory> poolFactory = Create<NrSlCommResourcePoolFactory>();
    std::vector<std::bitset<1>> slBitmap = {1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1};
    poolFactory->SetSlTimeResources(slBitmap);
    poolFactory->SetSlSensingWindow(100); // T0 in ms
    poolFactory->SetSlSelectionWindow(5); // T2min in slots
    poolFactory->SetSlFreqResourcePscch(10);
    poolFactory->SetSlSubchannelSize(p.bwPRBs);
    poolFactory->SetSlMaxNumPerReserve(3);
    std::list<uint16_t> rriList = {0, 20, 50, 100}; // ms; must cover all flow RRIs
    poolFactory->SetSlResourceReservePeriodList(rriList);
    // PSFCH: 0 = disabled (no HARQ feedback). 1/2/4 = period in slots.
    poolFactory->SetSlPsfchPeriod(p.psfchPeriod);
    poolFactory->SetSlMinTimeGapPsfch(3); // standard 3-slot gap (T_PSFCH_Min)

    LteRrcSap::SlResourcePoolNr slPool = poolFactory->CreatePool();

    LteRrcSap::SlResourcePoolIdNr poolId;
    poolId.id = 0;
    LteRrcSap::SlResourcePoolConfigNr poolCfg;
    poolCfg.haveSlResourcePoolConfigNr = true;
    poolCfg.slResourcePoolId = poolId;
    poolCfg.slResourcePool = slPool;

    LteRrcSap::SlBwpPoolConfigCommonNr bwpPoolCfg;
    bwpPoolCfg.slTxPoolSelectedNormal[poolId.id] = poolCfg;

    LteRrcSap::Bwp bwp;
    bwp.numerology = p.mu;
    bwp.symbolsPerSlots = 14;
    bwp.rbPerRbg = 1;
    bwp.bandwidth = fullBand100kHz;

    LteRrcSap::SlBwpGeneric slBwpGeneric;
    slBwpGeneric.bwp = bwp;
    slBwpGeneric.slLengthSymbols = LteRrcSap::GetSlLengthSymbolsEnum(14);
    slBwpGeneric.slStartSymbol = LteRrcSap::GetSlStartSymbolEnum(0);

    LteRrcSap::SlBwpConfigCommonNr slBwpCfg;
    slBwpCfg.haveSlBwpGeneric = true;
    slBwpCfg.slBwpGeneric = slBwpGeneric;
    slBwpCfg.haveSlBwpPoolConfigCommonNr = true;
    slBwpCfg.slBwpPoolConfigCommonNr = bwpPoolCfg;

    LteRrcSap::SlFreqConfigCommonNr slFreqCfg;
    for (const auto& bwpId : bwpIdContainer)
    {
        slFreqCfg.slBwpList[bwpId] = slBwpCfg;
    }

    // TDD pattern: "DL|DL|DL|F|UL|UL|UL|UL|UL|UL|" — 6 DL + 1 flex + 3 UL
    LteRrcSap::TddUlDlConfigCommon tddCfg;
    tddCfg.tddPattern = "DL|DL|DL|F|UL|UL|UL|UL|UL|UL|";

    LteRrcSap::SlPreconfigGeneralNr slPreGen;
    slPreGen.slTddConfig = tddCfg;

    // slMaxTxTransNumPssch ∈ [1..32]: 1 disables retx; default 5 = 1 initial + 4 retx.
    LteRrcSap::SlPsschTxParameters psschParams;
    psschParams.slMaxTxTransNumPssch = p.maxNumTx;
    LteRrcSap::SlPsschTxConfigList psschTxList;
    psschTxList.slPsschTxParameters[0] = psschParams;

    LteRrcSap::SlUeSelectedConfig slUeSel;
    slUeSel.slProbResourceKeep = 0.0;
    slUeSel.slPsschTxConfigList = psschTxList;

    LteRrcSap::SidelinkPreconfigNr slPreConfig;
    slPreConfig.slPreconfigGeneral = slPreGen;
    slPreConfig.slUeSelectedPreConfig = slUeSel;
    slPreConfig.slPreconfigFreqInfoList[0] = slFreqCfg;

    ctx.sl->InstallNrSlPreConfiguration(ctx.ueNetDev, slPreConfig);

    // -----------------------------------------------------------------------
    // 6. IP stack (IPv4) + default routes via the EPC gateway.
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

    NS_LOG_INFO("NR-SL stack built: mu=" << p.mu << " bwPRBs=" << p.bwPRBs
                                          << " mcs=" << p.mcs);
    return ctx;
}

} // namespace ns3
