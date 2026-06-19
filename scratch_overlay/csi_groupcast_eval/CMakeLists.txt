# scratch/csi_groupcast_eval/CMakeLists.txt
#
# SUMO-driven NR-V2X groupcast evaluator with geometry-aware NLOSv channel.
# All shared modules live in scratch_common; only the sim-specific main is here.

build_exec(
  EXECNAME csi_groupcast_eval
  EXECNAME_PREFIX scratch_csi_groupcast_eval_
  SOURCE_FILES
    csi_groupcast_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/csi_groupcast_eval/
)
