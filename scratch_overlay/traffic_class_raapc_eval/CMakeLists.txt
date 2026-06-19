# scratch/traffic_class_raapc_eval/CMakeLists.txt
#
# SINR-based RA-APC-Groupcast counterpart to traffic_class_adaptive_eval.
# All shared modules live in scratch_common.

build_exec(
  EXECNAME traffic_class_raapc_eval
  EXECNAME_PREFIX scratch_traffic_class_raapc_eval_
  SOURCE_FILES
    traffic_class_raapc_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/traffic_class_raapc_eval/
)
