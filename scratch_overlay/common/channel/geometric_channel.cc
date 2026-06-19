/*
 * geometric_channel.cc
 *
 * Replaces the channel-condition model on every BWP with the geometric NLOSv
 * wrapper.  The replacement happens in two places per BWP because NrHelper
 * sets the same CCM pointer on:
 *
 *   (a) ThreeGppPropagationLossModel::SetChannelConditionModel()
 *   (b) ThreeGppChannelModel (inner model of ThreeGppSpectrumPropagationLossModel)
 *       ::SetChannelConditionModel()
 *
 * We create one new wrapper per BWP, seed it with the existing CCM, and push
 * it onto both objects so they stay consistent.
 */

#include "geometric_channel.h"

#include "ns3/log.h"
#include "ns3/three-gpp-channel-model.h"
#include "ns3/three-gpp-propagation-loss-model.h"
#include "ns3/three-gpp-spectrum-propagation-loss-model.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("GeometricChannel");

void
ApplyGeometricNlosv(OperationBandInfo& band, const GeomChannelCfg& cfg)
{
    for (const auto& cc : band.m_cc)
    {
        for (const auto& bwp : cc->m_bwp)
        {
            // --- (a) Propagation loss model ---
            auto propLoss =
                DynamicCast<ThreeGppPropagationLossModel>(bwp->m_propagation);
            if (!propLoss)
            {
                NS_LOG_WARN("GeometricChannel: BWP propagation is not a "
                            "ThreeGppPropagationLossModel — skipping.");
                continue;
            }

            Ptr<ChannelConditionModel> innerCcm = propLoss->GetChannelConditionModel();

            auto wrapper = CreateObject<ThreeGppGeometricNlosvModel>();
            wrapper->SetWrappedModel(innerCcm);
            wrapper->SetBlockerDimensions(cfg.blockerLengthM, cfg.blockerWidthM);
            if (cfg.blockerNodes.GetN() > 0)
            {
                // Trace-replay mode: query node MobilityModel positions directly.
                wrapper->SetBlockerNodes(cfg.blockerNodes);
            }
            else
            {
                // SUMO/TraCI mode: look up vehicle positions via TraCI node map.
                wrapper->SetBlockerVehicleIds(cfg.blockerSumoIds);
                if (cfg.traci)
                    wrapper->SetTraciClient(cfg.traci);
            }

            propLoss->SetChannelConditionModel(wrapper);

            // --- (b) Spectrum propagation loss model / ThreeGppChannelModel ---
            auto specProp =
                DynamicCast<ThreeGppSpectrumPropagationLossModel>(bwp->m_3gppChannel);
            if (specProp)
            {
                auto chanModel =
                    DynamicCast<ThreeGppChannelModel>(specProp->GetChannelModel());
                if (chanModel)
                    chanModel->SetChannelConditionModel(wrapper);
                else
                    NS_LOG_WARN("GeometricChannel: inner channel model is not "
                                "ThreeGppChannelModel — spectrum CCM not replaced.");
            }

            NS_LOG_INFO("GeometricChannel: wrapped BWP " << +bwp->m_bwpId
                        << " with ThreeGppGeometricNlosvModel ("
                        << cfg.blockerSumoIds.size() << " blockers).");
        }
    }
}

} // namespace ns3
