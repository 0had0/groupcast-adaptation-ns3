# scratch/csi_groupcast_eval/CMakeLists.txt
#
# SUMO-driven NR-V2X groupcast evaluator with geometry-aware NLOSv channel.
#
# Module layout:
#   csi_groupcast_eval.cc        — main(): CLI → mobility → stack → flows → KPI → run
#   mobility.cc                  — SUMO/TraCI-only node creation
#   nr_sl_stack.cc               — NrHelper + geometric-NLOSv + NrSlHelper + IP
#   geometric_channel.cc         — ApplyGeometricNlosv() — replaces BWP CCMs post-init
#   three-gpp-geometric-nlosv.cc — ThreeGppGeometricNlosvModel (Liang–Barsky segment test)
#   groupcast_flows.cc           — multi-flow bearer + OnOff/PacketSink apps
#   kpi_hooks.cc                 — trace callbacks + SQLiteOutput + V2xKpi wiring
#   v2x-kpi.cc                   — V2xKpi engine (from contrib/nr/examples)

build_exec(
  EXECNAME csi_groupcast_eval
  EXECNAME_PREFIX scratch_csi_groupcast_eval_
  SOURCE_FILES
    csi_groupcast_eval.cc
    mobility.cc
    nr_sl_stack.cc
    nr-sl-adaptive-mcs-scheduler.cc
    geometric_channel.cc
    three-gpp-geometric-nlosv.cc
    groupcast_flows.cc
    ../custom-traffic/custom-traffic-application.cc
    ../custom-traffic/custom-traffic-helper.cc
    kpi_hooks.cc
    v2x-kpi.cc
  LIBRARIES_TO_LINK
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/csi_groupcast_eval/
)
