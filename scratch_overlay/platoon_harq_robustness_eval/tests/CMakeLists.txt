# tests/CMakeLists.txt -- sanity and validity tests of platoon_harq_robustness_eval (see TEST_PLAN.md)
#
# One executable per test: ninja target scratch_platoon_harq_robustness_eval_test_<ID>_<name>, binary
# build/scratch/platoon_harq_robustness_eval/tests/ns3.42-test_<ID>_<name>-default.
# Exit code: 0 = no FAIL, 1 = FAIL, 77 = all SKIP.  Each test writes tests/results/<ID>_<name>.json.
#
# Kinds:  PURE  analysis of run outputs only (links ptest_common, no ns-3)
#         NS3   links scratch_common + the ns-3 libraries
#         HSEL  NS3 + ../helper_select.cc
#         MICRO NS3 + common/micro_sl.cc (in-process stock-NR sidelink scenario)

set(PT_SRC ${CMAKE_CURRENT_SOURCE_DIR})

add_library(ptest_common STATIC
  common/harness.cc
  common/csv.cc
  common/stats.cc
  common/run_outputs.cc
  common/runsets.cc
  common/synth_trace.cc
  common/spectrum_accounting.cc
  common/analysis_kit.cc
)
set_target_properties(ptest_common PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ptest_common PUBLIC ${PT_SRC})
target_compile_definitions(ptest_common PUBLIC PTEST_SRC_DIR="${PT_SRC}")
target_compile_features(ptest_common PUBLIC cxx_std_20)

set(PTEST_LIST
  "unit|A01_rrc_sci_compliance|NS3"
  "unit|A02_psfch_timing|MICRO"
  "workflow|A03_nr_examples_regression|MICRO"
  "workflow|A04_patch_gating|PURE"
  "unit|A05_env_guard|PURE"
  "workflow|A06_determinism|PURE"
  "workflow|B01_blind_workflow|PURE"
  "workflow|B02_option1_workflow|PURE"
  "workflow|B03_option2_workflow|MICRO"
  "workflow|B04_feedback_binding|MICRO"
  "workflow|B05_harq_process_management|PURE"
  "workflow|B06_sps_harq_interaction|PURE"
  "workflow|C01_helper_signalling|PURE"
  "workflow|C02_relay_gating|PURE"
  "workflow|C03_option2_helper_silence|PURE"
  "workflow|C04_relay_combining|PURE"
  "workflow|C05_option1_helper_nack|PURE"
  "workflow|C06_cross_source_combining|NS3"
  "workflow|C07_stale_harq_state|PURE"
  "unit|D01_kuo_objective|HSEL"
  "campaign_checks|D02_selection_invariants|PURE"
  "campaign_checks|D03_selection_accounting|PURE"
  "campaign_checks|E01_kpi_recompute|PURE"
  "campaign_checks|E02_pir|PURE"
  "campaign_checks|E03_channel_variation_per_rx|PURE"
  "campaign_checks|E04_background_interference|PURE"
  "campaign_checks|E05_traffic_classes|PURE"
  "campaign_checks|E06_spectrum_usage_nodes|PURE"
  "campaign_checks|F01_sbsps_behaviour|PURE"
  "campaign_checks|F02_dynamic_behaviour|PURE"
  "campaign_checks|F03_sps_vs_dynamic|PURE"
  "campaign_checks|F04_retx_resource_constraints|PURE"
  "campaign_checks|G01_blind_effectiveness|PURE"
  "campaign_checks|G02_harq_upper_bound|PURE"
  "campaign_checks|G03_spectrum_gain_prr_parity|PURE"
  "campaign_checks|G04_option2_helper_protocol_scale|PURE"
  "campaign_checks|G05_helper_reliability_worst_rx|PURE"
  "campaign_checks|G06_distance_link_conditions|PURE"
  "campaign_checks|G07_harq_tradeoffs|PURE"
  "campaign_checks|G08_silent_helper_gap|PURE"
)

foreach(entry ${PTEST_LIST})
  string(REPLACE "|" ";" parts "${entry}")
  list(GET parts 0 pt_sub)
  list(GET parts 1 pt_name)
  list(GET parts 2 pt_kind)
  set(pt_src ${pt_sub}/${pt_name}.test.cc)
  if(NOT EXISTS ${PT_SRC}/${pt_src})
    message(STATUS "ptest: ${pt_src} not written yet, skipped")
    continue()
  endif()
  set(pt_sources ${pt_src})
  set(pt_libs ptest_common)
  if(NOT pt_kind STREQUAL "PURE")
    list(APPEND pt_libs scratch_common "${ns3-libs}" "${ns3-contrib-libs}")
  endif()
  if(pt_kind STREQUAL "HSEL")
    list(APPEND pt_sources ../helper_select.cc)
  endif()
  if(pt_kind STREQUAL "MICRO")
    list(APPEND pt_sources common/micro_sl.cc)
  endif()
  build_exec(
    EXECNAME test_${pt_name}
    EXECNAME_PREFIX scratch_platoon_harq_robustness_eval_
    SOURCE_FILES ${pt_sources}
    LIBRARIES_TO_LINK ${pt_libs}
    EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/platoon_harq_robustness_eval/tests/
  )
  if(pt_kind STREQUAL "HSEL")
    target_include_directories(scratch_platoon_harq_robustness_eval_test_${pt_name} PRIVATE ${PT_SRC}/..)
  endif()
endforeach()
