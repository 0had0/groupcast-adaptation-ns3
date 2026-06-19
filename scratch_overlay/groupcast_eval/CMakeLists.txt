# scratch/groupcast_eval/CMakeLists.txt
#
# Baseline NR-V2X groupcast evaluator (no geometric channel, SUMO optional).
# All shared modules live in scratch_common; only the sim-specific main is here.

build_exec(
  EXECNAME groupcast_eval
  EXECNAME_PREFIX scratch_groupcast_eval_
  SOURCE_FILES
    groupcast_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/groupcast_eval/
)
