# scratch/eval_template/CMakeLists.txt
#
# Template CMakeLists — replace SIMNAME with the new simulation directory name.

build_exec(
  EXECNAME eval_template
  EXECNAME_PREFIX scratch_eval_template_
  SOURCE_FILES
    eval_template.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/eval_template/
)
