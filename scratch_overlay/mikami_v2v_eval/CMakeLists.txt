# scratch/mikami_v2v_eval/CMakeLists.txt
#
# Mikami et al. 2020 (SoftBank) sidelink-Mode-2 reproduction for ns-3.42.
# Composition matches groupcast_eval — pulls scratch_common.

build_exec(
  EXECNAME mikami_v2v_eval
  EXECNAME_PREFIX scratch_mikami_v2v_eval_
  SOURCE_FILES
    mikami_v2v_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/mikami_v2v_eval/
)