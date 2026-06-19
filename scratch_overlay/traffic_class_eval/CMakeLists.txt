# scratch/traffic_class_eval/CMakeLists.txt
#
# Per-RX KPI characterisation across TrafficClass profiles.
# All shared modules live in scratch_common.

build_exec(
  EXECNAME traffic_class_eval
  EXECNAME_PREFIX scratch_traffic_class_eval_
  SOURCE_FILES
    traffic_class_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/traffic_class_eval/
)
