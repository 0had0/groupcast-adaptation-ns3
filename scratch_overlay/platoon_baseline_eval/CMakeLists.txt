# scratch/platoon_baseline_eval/CMakeLists.txt
#
# Fixed-MCS-14 baseline evaluation over three SUMO platoon scenarios.
# All shared modules live in scratch_common.

build_exec(
  EXECNAME platoon_baseline_eval
  EXECNAME_PREFIX scratch_platoon_baseline_eval_
  SOURCE_FILES
    platoon_baseline_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/platoon_baseline_eval/
)
