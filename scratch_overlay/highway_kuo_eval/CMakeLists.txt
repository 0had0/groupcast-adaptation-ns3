# scratch/highway_kuo_eval/CMakeLists.txt
#
# Template CMakeLists — replace SIMNAME with the new simulation directory name.

build_exec(
  EXECNAME highway_kuo_eval
  EXECNAME_PREFIX scratch_highway_kuo_eval_
  SOURCE_FILES
    highway_kuo_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/highway_kuo_eval/
)
