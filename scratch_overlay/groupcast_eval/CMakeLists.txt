# scratch/groupcast_eval/CMakeLists.txt
#
# Module layout:
#   groupcast_eval.cc  — main(): CLI → mobility → NR/SL stack → flows → traces → run
#   mobility.cc        — node creation + SUMO/highway mobility
#   nr_sl_stack.cc     — NrHelper + NrSlHelper + resource pool + IP stack
#   groupcast_flows.cc — 3-flow bearer activation + OnOff/PacketSink apps
#   kpi_hooks.cc       — trace callbacks + SQLiteOutput + V2xKpi wiring
#   v2x-kpi.cc         — V2xKpi (copied from contrib/nr/examples/nr-v2x-examples/)

build_exec(
  EXECNAME groupcast_eval
  EXECNAME_PREFIX scratch_groupcast_eval_
  SOURCE_FILES
    groupcast_eval.cc
    mobility.cc
    nr_sl_stack.cc
    groupcast_flows.cc
    kpi_hooks.cc
    v2x-kpi.cc
  LIBRARIES_TO_LINK
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/groupcast_eval/
)
