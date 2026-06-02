# scratch/traffic_class_eval/CMakeLists.txt
#
# Per-RX KPI characterisation across TrafficClass profiles.
# Reuses every support file from csi_groupcast_eval via relative source paths.

build_exec(
  EXECNAME traffic_class_eval
  EXECNAME_PREFIX scratch_traffic_class_eval_
  SOURCE_FILES
    traffic_class_eval.cc
    ../csi_groupcast_eval/mobility.cc
    ../csi_groupcast_eval/nr_sl_stack.cc
    ../csi_groupcast_eval/nr-sl-adaptive-mcs-scheduler.cc
    ../csi_groupcast_eval/geometric_channel.cc
    ../csi_groupcast_eval/three-gpp-geometric-nlosv.cc
    ../csi_groupcast_eval/groupcast_flows.cc
    ../csi_groupcast_eval/kpi_hooks.cc
    ../csi_groupcast_eval/v2x-kpi.cc
    ../custom-traffic/custom-traffic-application.cc
    ../custom-traffic/custom-traffic-helper.cc
  LIBRARIES_TO_LINK
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/traffic_class_eval/
)
