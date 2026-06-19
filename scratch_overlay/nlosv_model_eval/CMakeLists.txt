# scratch/nlosv_model_eval/CMakeLists.txt
#
# Standalone probe comparing probabilistic vs geometric NLOSv channel models.
# All shared modules (including three-gpp-geometric-nlosv) live in scratch_common.

build_exec(
  EXECNAME nlosv_model_eval
  EXECNAME_PREFIX scratch_nlosv_model_eval_
  SOURCE_FILES
    nlosv_model_eval.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/nlosv_model_eval/
)
