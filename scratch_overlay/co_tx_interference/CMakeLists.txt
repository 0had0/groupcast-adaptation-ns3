# scratch/co_tx_interference/CMakeLists.txt
#
# 9-vehicle platoon reTX-storm study — co-TX interference variant.
# Fork of relay_storm_eval with --relayMode {forward|noForward} and
# --relayStageDelayMs flags for the 2x2 sweep.

build_exec(
  EXECNAME co_tx_interference
  EXECNAME_PREFIX scratch_co_tx_interference_
  SOURCE_FILES
    co_tx_interference.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/co_tx_interference/
)