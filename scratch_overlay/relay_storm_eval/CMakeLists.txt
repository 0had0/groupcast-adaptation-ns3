# scratch/relay_storm_eval/CMakeLists.txt
#
# 9-vehicle platoon reTX-storm study with same-L2ID relay and MCD feedback gate.

build_exec(
  EXECNAME relay_storm_eval
  EXECNAME_PREFIX scratch_relay_storm_eval_
  SOURCE_FILES
    relay_storm_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/relay_storm_eval/
)
