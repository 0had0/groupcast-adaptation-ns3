# scratch/relay_mcs_resource_eval/CMakeLists.txt
#
# Template CMakeLists — replace SIMNAME with the new simulation directory name.

build_exec(
  EXECNAME relay_mcs_resource_eval
  EXECNAME_PREFIX scratch_relay_mcs_resource_eval_
  SOURCE_FILES
    relay_mcs_resource_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/relay_mcs_resource_eval/
)
