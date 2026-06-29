# scratch/relay_l2id_compare/CMakeLists.txt
#
# Unified relay-design comparison (none | sameL2 | fixed | broadcast) for NR-V2X
# mode-2 groupcast on a moving platoon with the MCD feedback gate.
# All shared modules live in scratch_common; only the sim-specific main is here.

build_exec(
  EXECNAME relay_l2id_compare
  EXECNAME_PREFIX scratch_relay_l2id_compare_
  SOURCE_FILES
    relay_l2id_compare.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/relay_l2id_compare/
)
