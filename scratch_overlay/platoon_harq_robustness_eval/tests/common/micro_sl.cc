#include "common/micro_sl.h"

#include "common/harness.h"

#include "ns3/antenna-module.h"
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/lte-sl-tft.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/nr-module.h"
#include "ns3/point-to-point-epc-helper.h"

#include <bitset>
#include <unistd.h>
#include <sys/stat.h>
#include <cmath>
#include <set>

namespace ptest
{

using namespace ns3;

namespace
{

struct Ctx
{
    MicroLog* log{nullptr};
    uint32_t mu{1};
    std::vector<Ptr<NrSlUeMac>> mac;
    std::vector<Ptr<NrUePhy>> phy;
    std::vector<int> rnti; // filled at run time
    const MicroCfg* cfg{nullptr};
};
Ctx g;

int64_t
SlotOf(double tS)
{
    // slot duration 1 ms / 2^mu
    return static_cast<int64_t>(std::floor(tS * 1000.0 * (1u << g.mu) + 1e-6));
}

int64_t
SlotOfSfn(uint32_t frame, uint32_t sub, uint32_t slot)
{
    return SfnSf(frame, sub, slot, g.mu).Normalize();
}

void
OnPsschSched(SlPsschUeMacStatParameters p)
{
    g.log->tx.push_back({Simulator::Now().GetSeconds(), SlotOfSfn(p.frameNum, p.subframeNum, p.slotNum), p.harqId,
                         p.ndi, p.rv, p.rbStart, p.rbLength});
}

void
OnRxPssch(int rx, SlRxDataPacketTraceParams p)
{
    g.log->rx.push_back({Simulator::Now().GetSeconds(), rx, SlotOfSfn(p.m_frameNum, p.m_subframeNum, p.m_slotNum),
                         p.m_ndi, p.m_rv, p.m_corrupt, p.m_sci2Corrupted, p.m_sinr});
}

void
OnTxFeedback(int rx, Time /*duration*/)
{
    g.log->fbTx.push_back({Simulator::Now().GetSeconds(), rx, SlotOf(Simulator::Now().GetSeconds())});
}

void
OnRxHarqFeedback(const SlHarqInfo& i)
{
    g.log->fbRx.push_back({Simulator::Now().GetSeconds(), i.m_harqProcessId, i.IsReceivedOk(), i.m_rnti});
}

void
OnHarqTimeout(uint8_t harq)
{
    g.log->timeouts.push_back({Simulator::Now().GetSeconds(), harq});
}

/// wraps the PHY's HARQ feedback callback: logs the decision, optionally drops it, then enqueues it
class FbShim : public Object
{
  public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ptest::MicroFbShim").SetParent<Object>();
        return tid;
    }
    void Setup(Ptr<NrUePhy> phy, int rx)
    {
        m_phy = phy;
        m_rx = rx;
    }
    void OnFeedback(const SlHarqInfo& info)
    {
        g.log->fbQueued.push_back({Simulator::Now().GetSeconds(), m_rx, info.m_harqProcessId, info.IsReceivedOk(),
                                   info.m_txRnti, info.m_rnti});
        if (g.cfg->feedbackFilter && !g.cfg->feedbackFilter(m_rx, info.m_harqProcessId, info.IsReceivedOk()))
        {
            return;
        }
        m_phy->EnqueueSlHarqFeedback(info);
    }

  private:
    Ptr<NrUePhy> m_phy;
    int m_rx{0};
};

void
OnAppRx(int rx, Ptr<const Packet> p, const Address&, const Address&, const SeqTsSizeHeader& h)
{
    g.log->appRx[rx].push_back(h.GetSeq());
}

} // namespace

bool
RunMicro(const MicroCfg& cfg, MicroLog& log, std::string* err)
{
    // the NR helper writes NrDlMacStats.txt / NrUlMacStats.txt into the process cwd: run inside tests/out/micro (not tests/)
    {
        const std::string dir = OutDir() + "/micro";
        mkdir(OutDir().c_str(), 0755);
        mkdir(dir.c_str(), 0755);
        if (chdir(dir.c_str()) != 0)
        {
            // keep the old cwd if the directory cannot be entered
        }
    }
    log = MicroLog();
    g = Ctx();
    g.log = &log;
    g.mu = cfg.mu;
    g.cfg = &cfg;
    const int nRx = static_cast<int>(cfg.rxDistM.size());
    log.slotsPerSubframe = 1 << cfg.mu;
    log.appRx.assign(nRx, {});

    RngSeedManager::SetSeed(cfg.seed);
    RngSeedManager::SetRun(cfg.seed);

    NodeContainer ues;
    ues.Create(1 + nRx);
    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    Ptr<ListPositionAllocator> pos = CreateObject<ListPositionAllocator>();
    pos->Add(Vector(0.0, 0.0, 1.5));
    for (int k = 0; k < nRx; ++k)
    {
        const double th = 0.3 + 2.0 * M_PI * k / std::max(1, nRx);
        pos->Add(Vector(cfg.rxDistM[k] * std::cos(th), cfg.rxDistM[k] * std::sin(th), 1.5));
    }
    mobility.SetPositionAllocator(pos);
    mobility.Install(ues);

    Ptr<NrPointToPointEpcHelper> epcHelper = CreateObject<NrPointToPointEpcHelper>();
    Ptr<NrHelper> nrHelper = CreateObject<NrHelper>();
    nrHelper->SetEpcHelper(epcHelper);

    BandwidthPartInfoPtrVector allBwps;
    CcBwpCreator ccBwpCreator;
    CcBwpCreator::SimpleOperationBandConf bandConfSl(5.89e9, 200 /* 20 MHz */, 1, BandwidthPartInfo::RMa_LoS);
    OperationBandInfo bandSl = ccBwpCreator.CreateOperationBandContiguousCc(bandConfSl);
    Config::SetDefault("ns3::ThreeGppChannelModel::UpdatePeriod", TimeValue(MilliSeconds(10)));
    nrHelper->SetChannelConditionModelAttribute("UpdatePeriod", TimeValue(MilliSeconds(10)));
    nrHelper->SetPathlossAttribute("ShadowingEnabled", BooleanValue(false));
    nrHelper->InitializeOperationBand(&bandSl);
    allBwps = CcBwpCreator::GetAllBwps({bandSl});

    epcHelper->SetAttribute("S1uLinkDelay", TimeValue(MilliSeconds(0)));
    nrHelper->SetUeAntennaAttribute("NumRows", UintegerValue(1));
    nrHelper->SetUeAntennaAttribute("NumColumns", UintegerValue(2));
    nrHelper->SetUeAntennaAttribute("AntennaElement", PointerValue(CreateObject<IsotropicAntennaModel>()));
    nrHelper->SetUePhyAttribute("TxPower", DoubleValue(cfg.txPowerDbm));
    nrHelper->SetUeMacTypeId(NrSlUeMac::GetTypeId());
    nrHelper->SetUeMacAttribute("EnableSensing", BooleanValue(false));
    nrHelper->SetUeMacAttribute("T1", UintegerValue(2));
    nrHelper->SetUeMacAttribute("T2", UintegerValue(33));
    nrHelper->SetUeMacAttribute("ActivePoolId", UintegerValue(0));
    nrHelper->SetBwpManagerTypeId(TypeId::LookupByName("ns3::NrSlBwpManagerUe"));
    nrHelper->SetUeBwpManagerAlgorithmAttribute("GBR_MC_PUSH_TO_TALK", UintegerValue(0));
    std::set<uint8_t> bwpIds{0};

    NetDeviceContainer devs = nrHelper->InstallUeDevice(ues, allBwps);
    for (auto it = devs.Begin(); it != devs.End(); ++it)
    {
        DynamicCast<NrUeNetDevice>(*it)->UpdateConfig();
    }

    Ptr<NrSlHelper> slHelper = CreateObject<NrSlHelper>();
    slHelper->SetEpcHelper(epcHelper);
    slHelper->SetSlErrorModel("ns3::NrEesmIrT1");
    slHelper->SetUeSlAmcAttribute("AmcModel", EnumValue(NrAmc::ErrorModel));
    slHelper->SetNrSlSchedulerTypeId(NrSlUeMacSchedulerFixedMcs::GetTypeId());
    slHelper->SetUeSlSchedulerAttribute("Mcs", UintegerValue(cfg.mcs));
    slHelper->PrepareUeForSidelink(devs, bwpIds);

    Ptr<NrSlCommResourcePoolFactory> pf = Create<NrSlCommResourcePoolFactory>();
    std::vector<std::bitset<1>> bitmap = {1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1};
    if (cfg.dedicated)
    {
        bitmap.assign(10, std::bitset<1>(1));
    }
    pf->SetSlTimeResources(bitmap);
    pf->SetSlSensingWindow(100);
    pf->SetSlSelectionWindow(5);
    pf->SetSlFreqResourcePscch(10);
    pf->SetSlSubchannelSize(10);
    pf->SetSlMaxNumPerReserve(cfg.maxNumPerReserve);
    pf->SetSlPsfchPeriod(cfg.psfchPeriod);
    pf->SetSlMinTimeGapPsfch(3);
    std::list<uint16_t> rri = {0, 100};
    pf->SetSlResourceReservePeriodList(rri);
    LteRrcSap::SlResourcePoolNr pool = pf->CreatePool();
    LteRrcSap::SlResourcePoolConfigNr poolCfg;
    poolCfg.haveSlResourcePoolConfigNr = true;
    LteRrcSap::SlResourcePoolIdNr pid;
    pid.id = 0;
    poolCfg.slResourcePoolId = pid;
    poolCfg.slResourcePool = pool;
    LteRrcSap::SlBwpPoolConfigCommonNr bwpPool;
    bwpPool.slTxPoolSelectedNormal[0] = poolCfg;
    LteRrcSap::Bwp bwp;
    bwp.numerology = cfg.mu;
    bwp.symbolsPerSlots = 14;
    bwp.rbPerRbg = 1;
    bwp.bandwidth = 200;
    LteRrcSap::SlBwpGeneric gen;
    gen.bwp = bwp;
    gen.slLengthSymbols = LteRrcSap::GetSlLengthSymbolsEnum(14);
    gen.slStartSymbol = LteRrcSap::GetSlStartSymbolEnum(0);
    LteRrcSap::SlBwpConfigCommonNr bwpCfg;
    bwpCfg.haveSlBwpGeneric = true;
    bwpCfg.slBwpGeneric = gen;
    bwpCfg.haveSlBwpPoolConfigCommonNr = true;
    bwpCfg.slBwpPoolConfigCommonNr = bwpPool;
    LteRrcSap::SlFreqConfigCommonNr freq;
    for (auto b : bwpIds)
    {
        freq.slBwpList[b] = bwpCfg;
    }
    LteRrcSap::TddUlDlConfigCommon tdd;
    tdd.tddPattern = cfg.dedicated ? "UL|UL|UL|UL|UL|UL|UL|UL|UL|UL|" : "DL|DL|DL|F|UL|UL|UL|UL|UL|UL|";
    LteRrcSap::SlPreconfigGeneralNr general;
    general.slTddConfig = tdd;
    LteRrcSap::SlUeSelectedConfig sel;
    sel.slProbResourceKeep = 0;
    LteRrcSap::SlPsschTxParameters tx;
    tx.slMaxTxTransNumPssch = cfg.maxNumTx;
    LteRrcSap::SlPsschTxConfigList txl;
    txl.slPsschTxParameters[0] = tx;
    sel.slPsschTxConfigList = txl;
    LteRrcSap::SidelinkPreconfigNr pre;
    pre.slPreconfigGeneral = general;
    pre.slUeSelectedPreConfig = sel;
    pre.slPreconfigFreqInfoList[0] = freq;
    slHelper->InstallNrSlPreConfiguration(devs, pre);

    int64_t stream = 1;
    stream += nrHelper->AssignStreams(devs, stream);
    stream += slHelper->AssignStreams(devs, stream);

    InternetStackHelper internet;
    internet.Install(ues);
    Ipv4InterfaceContainer iface = epcHelper->AssignUeIpv4Address(devs);
    Ipv4StaticRoutingHelper rh;
    for (uint32_t u = 0; u < ues.GetN(); ++u)
    {
        rh.GetStaticRouting(ues.Get(u)->GetObject<Ipv4>())
            ->SetDefaultRoute(epcHelper->GetUeDefaultGatewayAddress(), 1);
    }

    SidelinkInfo si;
    if (cfg.cast == "groupcast")
    {
        si.m_castType = SidelinkInfo::CastType::Groupcast;
    }
    else if (cfg.cast == "nackonly")
    {
        si.m_castType = SidelinkInfo::CastType::GroupcastNegativeOnly;
    }
    else
    {
        si.m_castType = SidelinkInfo::CastType::Broadcast;
    }
    si.m_harqEnabled = cfg.harq;
    si.m_pdb = MilliSeconds(static_cast<int64_t>(cfg.pdbMs));
    si.m_dstL2Id = 224;
    si.m_dynamic = cfg.dynamic;
    si.m_rri = cfg.dynamic ? MilliSeconds(0) : MilliSeconds(static_cast<int64_t>(cfg.rriMs));
    Ipv4Address group("225.0.0.0");
    const uint16_t port = 8000;
    Time act = Seconds(2.0);
    NetDeviceContainer txDevs, rxDevs;
    txDevs.Add(devs.Get(0));
    for (int k = 0; k < nRx; ++k)
    {
        rxDevs.Add(devs.Get(1 + k));
    }
    slHelper->ActivateNrSlBearer(act, txDevs, Create<LteSlTft>(LteSlTft::Direction::TRANSMIT, group, si));
    slHelper->ActivateNrSlBearer(act, rxDevs, Create<LteSlTft>(LteSlTft::Direction::RECEIVE, group, si));

    // application: nPackets of pktBytes every intervalMs
    OnOffHelper client("ns3::UdpSocketFactory", InetSocketAddress(group, port));
    client.SetAttribute("EnableSeqTsSizeHeader", BooleanValue(true));
    client.SetAttribute("MaxBytes", UintegerValue(static_cast<uint64_t>(cfg.nPackets) * cfg.pktBytes));
    const double bps = cfg.pktBytes * 8.0 / (cfg.intervalMs / 1000.0);
    client.SetConstantRate(DataRate(static_cast<uint64_t>(bps)), cfg.pktBytes);
    ApplicationContainer ca = client.Install(ues.Get(0));
    ca.Start(Seconds(cfg.firstPacketS - cfg.intervalMs / 1000.0));
    const double simEnd = cfg.firstPacketS + cfg.nPackets * cfg.intervalMs / 1000.0 + cfg.extraSimS;
    ca.Stop(Seconds(simEnd));
    PacketSinkHelper sink("ns3::UdpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), port));
    sink.SetAttribute("EnableSeqTsSizeHeader", BooleanValue(true));
    NodeContainer sinks;
    for (int k = 0; k < nRx; ++k)
    {
        sinks.Add(ues.Get(1 + k));
    }
    ApplicationContainer sa = sink.Install(sinks);
    sa.Start(Seconds(2.0));
    for (int k = 0; k < nRx; ++k)
    {
        sa.Get(k)->TraceConnectWithoutContext("RxWithSeqTsSize", MakeBoundCallback(&OnAppRx, k));
    }

    // traces
    for (uint32_t i = 0; i < ues.GetN(); ++i)
    {
        Ptr<NrUeNetDevice> d = DynamicCast<NrUeNetDevice>(devs.Get(i));
        Ptr<NrSlUeMac> mac = d->GetMac(0)->GetObject<NrSlUeMac>();
        Ptr<NrUePhy> phy = d->GetPhy(0);
        g.mac.push_back(mac);
        g.phy.push_back(phy);
        if (i == 0)
        {
            mac->TraceConnectWithoutContext("SlPsschScheduling", MakeCallback(&OnPsschSched));
            PointerValue pv;
            mac->GetAttribute("NrSlUeMacHarq", pv);
            Ptr<NrSlUeMacHarq> harq = pv.Get<NrSlUeMacHarq>();
            if (harq)
            {
                harq->TraceConnectWithoutContext("RxHarqFeedback", MakeCallback(&OnRxHarqFeedback));
                harq->TraceConnectWithoutContext("Timeout", MakeCallback(&OnHarqTimeout));
            }
        }
        else
        {
            const int rx = static_cast<int>(i) - 1;
            phy->GetSpectrumPhy()->TraceConnectWithoutContext("RxPsschTraceUe", MakeBoundCallback(&OnRxPssch, rx));
            phy->GetSpectrumPhy()->TraceConnectWithoutContext("TxFeedbackTrace",
                                                              MakeBoundCallback(&OnTxFeedback, rx));
            Ptr<FbShim> shim = CreateObject<FbShim>();
            shim->Setup(phy, rx);
            phy->GetSpectrumPhy()->SetPhySlHarqFeedbackCallback(MakeCallback(&FbShim::OnFeedback, shim));
        }
    }

    Simulator::Stop(Seconds(simEnd));
    for (const auto& inj : cfg.inject)
    {
        std::vector<uint32_t> rxNodeIds;
        for (int k = 0; k < nRx; ++k)
        {
            rxNodeIds.push_back(ues.Get(1 + k)->GetId());
        }
        Simulator::Schedule(Seconds(inj.t), [inj, rxNodeIds]() {
            SlHarqInfo info;
            info.m_rnti = g.mac[1 + inj.rx]->GetRnti();
            info.m_txRnti = g.mac[0]->GetRnti();
            info.m_dstL2Id = 224;
            info.m_harqProcessId = static_cast<uint8_t>(inj.harq);
            info.m_bwpIndex = 0;
            info.m_harqStatus = inj.ack ? SlHarqInfo::ACK : SlHarqInfo::NACK;
            g.mac[0]->GetNrSlUePhySapUser()->ReceivePsfch(rxNodeIds[inj.rx], info);
        });
    }
    // read the pool once the RNTIs exist
    Simulator::Schedule(Seconds(2.2), []() {
        g.log->txRnti.clear();
        for (auto& m : g.mac)
        {
            g.log->txRnti.push_back(m->GetRnti());
        }
        Ptr<NrSlUeMac> m0 = g.mac[0];
        const int n = 16384;
        g.log->psfchMaskSlots = n;
        g.log->psfchSlotMask.assign(n, 0);
        for (int s = 0; s < n; ++s)
        {
            SfnSf sfn(0, 0, 0, g.mu);
            sfn.Add(s);
            g.log->psfchSlotMask[s] = m0->SlotHasPsfch(sfn) ? 1 : 0;
        }
    });
    Simulator::Run();
    // pool parameters
    {
        Ptr<NrSlUeMac> m0 = g.mac[0];
        SfnSf sfn(0, 0, 0, g.mu);
        int first = -1, second = -1;
        for (int s = 0; s < 64; ++s)
        {
            SfnSf x(0, 0, 0, g.mu);
            x.Add(s);
            if (m0->SlotHasPsfch(x))
            {
                (first < 0 ? first : second) = s;
                if (second >= 0)
                {
                    break;
                }
            }
        }
        log.psfchPeriodPool = (second >= 0 && first >= 0) ? (second - first) : -1;
        (void)sfn;
    }
    uint32_t sent = 0;
    for (auto& v : log.appRx)
    {
        sent = std::max<uint32_t>(sent, v.empty() ? 0 : *std::max_element(v.begin(), v.end()) + 1);
    }
    log.appSent = std::max<uint32_t>(sent, cfg.nPackets);
    Simulator::Destroy();
    g = Ctx();
    return true;
}

FailDistance
FindFailingDistance()
{
    FailDistance fd;
    MicroCfg c;
    c.rxDistM = {200, 300, 400, 500, 600, 700, 800, 900, 1000, 1200, 1500, 2000};
    c.nPackets = 60;
    c.intervalMs = 40.0;
    MicroLog lg;
    if (!RunMicro(c, lg))
    {
        return fd;
    }
    const int n = static_cast<int>(c.rxDistM.size());
    std::vector<long> ev(n, 0), cor(n, 0), s2(n, 0);
    for (const auto& e : lg.rx)
    {
        ++ev[e.rx];
        cor[e.rx] += (e.corrupt && !e.sci2Corrupt) ? 1 : 0;
        s2[e.rx] += e.sci2Corrupt ? 1 : 0;
    }
    for (int k = 0; k < n; ++k)
    {
        fd.table += std::to_string(static_cast<int>(c.rxDistM[k])) + "m: corrupt " + std::to_string(cor[k]) + "/" +
                    std::to_string(ev[k]) + " sci2bad " + std::to_string(s2[k]) + "; ";
        if (!fd.found && ev[k] >= 30)
        {
            const double cf = static_cast<double>(cor[k]) / ev[k];
            const double sf = static_cast<double>(s2[k]) / ev[k];
            if (cf >= 0.9 && sf <= 0.1)
            {
                fd.found = true;
                fd.distM = c.rxDistM[k];
                fd.corruptFrac = cf;
                fd.sci2Frac = sf;
                fd.events = ev[k];
            }
        }
    }
    return fd;
}

} // namespace ptest
