# scratch/platoon_harq_robustness_eval/CMakeLists.txt

build_exec(
  EXECNAME platoon_harq_robustness_eval
  EXECNAME_PREFIX scratch_platoon_harq_robustness_eval_
  SOURCE_FILES
    platoon_harq_robustness_eval.cc
    nack_only_harq.cc
    sumo_buildings.cc
    helper_select.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/platoon_harq_robustness_eval/
)
