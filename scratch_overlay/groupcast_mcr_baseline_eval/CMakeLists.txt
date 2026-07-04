# scratch_overlay/groupcast_mcr_baseline_eval/CMakeLists.txt
#
# Single-hop baseline (no relay) for the NR-V2X mode-2 groupcast platoon study.
# All shared modules live in scratch_common; only the sim-specific main is here.

build_exec(
  EXECNAME groupcast_mcr_baseline_eval
  EXECNAME_PREFIX scratch_groupcast_mcr_baseline_eval_
  SOURCE_FILES
    groupcast_mcr_baseline_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/groupcast_mcr_baseline_eval/
)