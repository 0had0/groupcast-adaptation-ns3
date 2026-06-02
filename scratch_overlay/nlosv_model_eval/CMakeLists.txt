# scratch/nlosv_model_eval/CMakeLists.txt
#
# Standalone probe comparing probabilistic (density-based) vs geometric
# (position-exact) NLOSv channel-condition determination.
#
#   nlosv_model_eval.cc          - main(): trace -> both models -> per-link CSV
#   three-gpp-geometric-nlosv.cc - ThreeGppGeometricNlosvModel (reused from
#                                  ../csi_groupcast_eval, not copied)
#
# ns3-contrib-libs is required: three-gpp-geometric-nlosv.h includes
# ns3/traci-client.h even though TraCI is unused by this probe.

build_exec(
  EXECNAME nlosv_model_eval
  EXECNAME_PREFIX scratch_nlosv_model_eval_
  SOURCE_FILES
    nlosv_model_eval.cc
    ../csi_groupcast_eval/three-gpp-geometric-nlosv.cc
  LIBRARIES_TO_LINK
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/nlosv_model_eval/
)
