// sl_stack_bler.cc
//
// See sl_stack_bler.h. Fixed-MCS NR-SL Mode-2 stack with deterministic
// LOS/NLOS/NLOSv control, realtime channel updates, and N-dependent NLOSv loss.

#include "sl_stack_bler.h"

#include "ns3/antenna-module.h"
#include "ns3/buildings-helper.h"
#include "ns3/channel-condition-model.h"
#include "ns3/config.h"
#include "ns3/ipv4-static-routing-helper.h"
#include "ns3/log.h"
#include "ns3/lte-ue-rrc.h"
#include "ns3/nr-amc.h"
#include "ns3/pointer.h"
#include "ns3/three-gpp-channel-model.h"
#include "ns3/three-gpp-propagation-loss-model.h"
#include "ns3/three-gpp-spectrum-propagation-loss-model.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("SlBlerSweep.NrSlStack");

namespace
{

// Set a fixed channel-condition model (AlwaysLos / NeverLos) on every BWP, on
// both the propagation-loss model and the inner spectrum channel model — the
// same two-place pattern used by relay_groupcast_eval/geometric_channel.cc.
void
SetFixedConditionAllBwps(OperationBandInfo& band, TypeId ccmTypeId)
{
    ObjectFactory f;
    f.SetTypeId(ccmTypeId);
    for (const auto& cc : band.m_cc)
    {
        for (const auto& bwp : cc->m_bwp)
        {
            auto propLoss = DynamicCast<ThreeGppPropagationLossModel>(bwp->m_propagation);
            if (!propLoss)
            {
                NS_LOG_WARN("SetFixedCondition: BWP propagation is not ThreeGpp — skipping.");
                continue;
            }
            Ptr<ChannelConditionModel> ccm = f.Create<ChannelConditionModel>();
            propLoss->SetChannelConditionModel(ccm);

            auto specProp = DynamicCast<ThreeGppSpectrumPropagationLossModel>(bwp->m_3gppChannel);
            if (specProp)
            {
                auto chanModel = DynamicCast<ThreeGppChannelModel>(specProp->GetChannelModel());
                if (chanModel)
                {
                    chanModel->SetChannelConditionModel(ccm);
                }
            }
        }
    }
}

} // namespace

SlStackCtx
BuildSlStackBler(const SlStackParams& p,
                 const GeomChannelCfg& geom,
                 SlChannelCond cond,
                 double channelUpdateMs,
                 const NodeContainer& nodes)
{
    SlStackCtx ctx;

    // -- 1. Band / BWP: 5.89 GHz (band n47); bandwidth scales with numerology. --
    const double centralFreqHz = 5.89e9;
    const uint16_t fullBand100kHz = static_cast<uint16_t>(200u << p.mu);

    CcBwpCreator::SimpleOperationBandConf bandConf(centralFreqHz,
                                                   fullBand100kHz,
                                                   1 /* numCcPerBand */,
                                                   p.scenario);
    CcBwpCreator ccBwpCreator;
    OperationBandInfo bandSl = ccBwpCreator.CreateOperationBandContiguousCc(bandConf);

    // -- 2. NrHelper: realtime channel updates + shadowing. --
    ctx.epc = CreateObject<NrPointToPointEpcHelper>();
    ctx.nr = CreateObject<NrHelper>();
    ctx.nr->SetEpcHelper(ctx.epc);
    ctx.epc->SetAttribute("S1uLinkDelay", TimeValue(MilliSeconds(0)));

    const double updMs = (channelUpdateMs > 0.0) ? channelUpdateMs : 10.0;
    const Time updPeriod = Seconds(updMs / 1000.0);
    // How often the spatial-channel (fast-fading) matrix is recomputed.
    Config::SetDefault("ns3::ThreeGppChannelModel::UpdatePeriod", TimeValue(updPeriod));
    // How often the LOS/NLOS/NLOSv condition is re-evaluated.
    ctx.nr->SetChannelConditionModelAttribute("UpdatePeriod", TimeValue(updPeriod));
    ctx.nr->SetPathlossAttribute("ShadowingEnabled", BooleanValue(p.shadowingEnabled));

    ctx.nr->InitializeOperationBand(&bandSl);

    // -- 3. Force the requested channel condition. --
    switch (cond)
    {
    case SlChannelCond::Los:
        SetFixedConditionAllBwps(bandSl, AlwaysLosChannelConditionModel::GetTypeId());
        break;
    case SlChannelCond::Nlos:
        SetFixedConditionAllBwps(bandSl, NeverLosChannelConditionModel::GetTypeId());
        break;
    case SlChannelCond::Nlosv:
        // Geometry forces the NLOSv condition whenever a blocker AABB intersects the
        // Tx→Rx segment. The 3GPP V2V model then applies its standard NLOSv pathloss
        // (LOS + one log-normal blockage term per TR 37.885 §6.4.1) — N does not
        // scale that additional loss, which is correct per spec.
        ApplyGeometricNlosv(bandSl, geom);
        break;
    }

    BandwidthPartInfoPtrVector allBwps = CcBwpCreator::GetAllBwps({bandSl});
    Packet::EnablePrinting();

    // -- 4. UE antenna + PHY attributes. --
    ctx.nr->SetUeAntennaAttribute("NumRows", UintegerValue(1));
    ctx.nr->SetUeAntennaAttribute("NumColumns", UintegerValue(2));
    ctx.nr->SetUeAntennaAttribute("AntennaElement",
                                  PointerValue(CreateObject<IsotropicAntennaModel>()));
    ctx.nr->SetUePhyAttribute("TxPower", DoubleValue(23.0));

    ctx.nr->SetUeMacTypeId(NrSlUeMac::GetTypeId());
    ctx.nr->SetUeMacAttribute("EnableSensing", BooleanValue(true));
    ctx.nr->SetUeMacAttribute("T1", UintegerValue(2));
    ctx.nr->SetUeMacAttribute("T2", UintegerValue(33));
    ctx.nr->SetUeMacAttribute("ActivePoolId", UintegerValue(0));
    ctx.nr->SetUeMacAttribute("SlThresPsschRsrp", IntegerValue(-128));

    const uint8_t bwpIdSl = 0;
    ctx.nr->SetBwpManagerTypeId(TypeId::LookupByName("ns3::NrSlBwpManagerUe"));
    ctx.nr->SetUeBwpManagerAlgorithmAttribute("GBR_MC_PUSH_TO_TALK", UintegerValue(bwpIdSl));
    std::set<uint8_t> bwpIdContainer{bwpIdSl};

    // -- 5. Install UE devices + assign streams. --
    BuildingsHelper::Install(nodes);
    ctx.ueNetDev = ctx.nr->InstallUeDevice(nodes, allBwps);
    for (auto it = ctx.ueNetDev.Begin(); it != ctx.ueNetDev.End(); ++it)
    {
        DynamicCast<NrUeNetDevice>(*it)->UpdateConfig();
    }

    ctx.stream = 10 * static_cast<int64_t>(p.seed) + 1;
    ctx.stream += ctx.nr->AssignStreams(ctx.ueNetDev, ctx.stream);

    // -- 6. NrSlHelper: NrEesmIrT1 error model + FixedMcs scheduler (AMC off). --
    ctx.sl = CreateObject<NrSlHelper>();
    ctx.sl->SetEpcHelper(ctx.epc);
    ctx.sl->SetSlErrorModel("ns3::NrEesmIrT1");
    ctx.sl->SetUeSlAmcAttribute("AmcModel", EnumValue(NrAmc::ErrorModel));
    ctx.sl->SetNrSlSchedulerTypeId(NrSlUeMacSchedulerFixedMcs::GetTypeId());
    ctx.sl->SetUeSlSchedulerAttribute("Mcs", UintegerValue(p.mcs));
    ctx.sl->PrepareUeForSidelink(ctx.ueNetDev, bwpIdContainer);
    ctx.stream += ctx.sl->AssignStreams(ctx.ueNetDev, ctx.stream);

    // -- 7. Resource pool: slBitmap "111111000111" (9/12 subframes for SL). --
    Ptr<NrSlCommResourcePoolFactory> poolFactory = Create<NrSlCommResourcePoolFactory>();
    std::vector<std::bitset<1>> slBitmap = {1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1};
    poolFactory->SetSlTimeResources(slBitmap);
    poolFactory->SetSlSensingWindow(100);
    poolFactory->SetSlSelectionWindow(5);
    poolFactory->SetSlFreqResourcePscch(10);
    poolFactory->SetSlSubchannelSize(p.bwPRBs);
    poolFactory->SetSlMaxNumPerReserve(3);
    std::list<uint16_t> rriList;
    rriList.push_back(0);
    for (uint16_t r = 10; r <= 100; r += 10)
    {
        rriList.push_back(r);
    }
    for (uint16_t r = 150; r <= 1000; r += 50)
    {
        if (r == 250)
        {
            continue; // LteRrcSap::GetSlResoResvPrdValue has no MS250 case
        }
        rriList.push_back(r);
    }
    poolFactory->SetSlResourceReservePeriodList(rriList);
    poolFactory->SetSlPsfchPeriod(p.psfchPeriod);
    poolFactory->SetSlMinTimeGapPsfch(3);

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
    for (const auto& bid : bwpIdContainer)
    {
        slFreqCfg.slBwpList[bid] = slBwpCfg;
    }

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
    slPreConfig.slPreconfigGeneral = slPreGen;
    slPreConfig.slUeSelectedPreConfig = slUeSel;
    slPreConfig.slPreconfigFreqInfoList[0] = slFreqCfg;

    ctx.sl->InstallNrSlPreConfiguration(ctx.ueNetDev, slPreConfig);

    // -- 8. IP stack + default routes. --
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

    // -- 9. RNTI -> nodeId map. --
    for (uint32_t i = 0; i < ctx.ueNetDev.GetN(); ++i)
    {
        auto dev = DynamicCast<NrUeNetDevice>(ctx.ueNetDev.Get(i));
        if (!dev)
        {
            continue;
        }
        auto rrc = dev->GetRrc();
        if (!rrc)
        {
            continue;
        }
        ctx.rntiToNode[rrc->GetRnti()] = nodes.Get(i)->GetId();
    }

    NS_LOG_INFO("NR-SL BLER stack: scenario=" << p.scenario << " mu=" << p.mu
                                              << " bwPRBs=" << p.bwPRBs << " mcs=" << p.mcs
                                              << " cond=" << static_cast<int>(cond)
                                              << " updMs=" << updMs);
    return ctx;
}

} // namespace ns3
