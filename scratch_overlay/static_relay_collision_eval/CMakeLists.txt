# scratch/static_relay_collision_eval/CMakeLists.txt
#
# Static-platoon NR-V2X two-hop relay collision & relay-PDB study.
# All shared modules live in scratch_common; only the sim-specific main is here.

build_exec(
  EXECNAME static_relay_collision_eval
  EXECNAME_PREFIX scratch_static_relay_collision_eval_
  SOURCE_FILES
    static_relay_collision_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/static_relay_collision_eval/
)
