# scratch_overlay/common/CMakeLists.txt
#
# Builds `scratch_common`, a STATIC library of the shared NR-V2X simulation
# building blocks (mobility, geometric channel, NR-SL stack + schedulers, flow
# / cast / group config, KPI engine + hooks, L2 relays, custom traffic).
#
# Simulations consume it by linking `scratch_common` and including modules with
# a root-on-include-path convention, e.g.
#
#     #include "common/stack/nr_sl_stack.h"
#     #include "common/flows/groupcast_flows.h"
#
# The PUBLIC include directory below (the parent of common/, i.e. the scratch
# root) makes those paths resolve in every consumer. See common/README.md and
# scratch_overlay/eval_template/ for the composition recipe.

# ---------------------------------------------------------------------------
# Optional compression codecs (zstd / lz4).
#
# When BOTH are found, kpi_hooks.cc is compiled with KPI_HOOKS_HAVE_CODEC_LIBS=1
# and the receiver-side decompression branch (used by traffic_class_compress_eval)
# is active for every consumer. When either is missing, the codecs are not
# linked and the decompression step is gracefully elided (magic-prefix detection
# still runs); this keeps the library buildable on machines without zstd/lz4.
# ---------------------------------------------------------------------------
find_library(ZSTD_LIB zstd PATHS /opt/local/lib /opt/homebrew/lib /usr/local/lib)
find_library(LZ4_LIB  lz4  PATHS /opt/local/lib /opt/homebrew/lib /usr/local/lib)
find_path(ZSTD_INCLUDE_DIR zstd.h PATHS /opt/local/include /opt/homebrew/include /usr/local/include)
find_path(LZ4_INCLUDE_DIR  lz4.h  PATHS /opt/local/include /opt/homebrew/include /usr/local/include)

set(SCRATCH_COMMON_HAVE_CODECS FALSE)
if(ZSTD_LIB AND LZ4_LIB AND ZSTD_INCLUDE_DIR AND LZ4_INCLUDE_DIR)
  set(SCRATCH_COMMON_HAVE_CODECS TRUE)
  message(STATUS "scratch_common: zstd/lz4 found — compression KPI branch ENABLED")
else()
  message(STATUS "scratch_common: zstd/lz4 not found — compression KPI branch DISABLED")
endif()

# ---------------------------------------------------------------------------
# Library sources (all module .cc; the adaptive-MCS controller is header-only).
# ---------------------------------------------------------------------------
add_library(scratch_common STATIC
  channel/geometric_channel.cc
  channel/three-gpp-geometric-nlosv.cc
  mobility/mobility.cc
  stack/nr_sl_stack.cc
  stack/nr-sl-adaptive-mcs-scheduler.cc
  stack/nr-sl-deterministic-scheduler.cc
  flows/groupcast_flows.cc
  kpi/kpi_hooks.cc
  kpi/v2x-kpi.cc
  mcd/mcr_feedback_gate.cc
  mcd/mcr_nack_only_gate.cc
  relay/boundary_relay_app.cc
  relay/fixed_relay_app.cc
  relay/same_l2id_relay_app.cc
  traffic/custom-traffic-application.cc
  traffic/custom-traffic-helper.cc
)

# Build position-independent so it can link into ns-3's shared-library world.
set_target_properties(scratch_common PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Root-on-include-path: the parent of common/ (the scratch root). Consumers
# inherit this, so `#include "common/<group>/<file>.h"` resolves everywhere.
target_include_directories(scratch_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/..)

# ns-3 module libraries — PUBLIC so consumers link them transitively.
target_link_libraries(scratch_common PUBLIC "${ns3-libs}" "${ns3-contrib-libs}")

if(SCRATCH_COMMON_HAVE_CODECS)
  target_compile_definitions(scratch_common PRIVATE KPI_HOOKS_HAVE_CODEC_LIBS=1)
  target_include_directories(scratch_common PRIVATE ${ZSTD_INCLUDE_DIR} ${LZ4_INCLUDE_DIR})
  target_link_libraries(scratch_common PUBLIC ${ZSTD_LIB} ${LZ4_LIB})
endif()

# ---------------------------------------------------------------------------
# Smoke-test executable for the custom-traffic module (preserves the old
# scratch_custom-traffic_test-custom-traffic target).
# ---------------------------------------------------------------------------
build_exec(
  EXECNAME test-custom-traffic
  EXECNAME_PREFIX scratch_common_
  SOURCE_FILES traffic/test-custom-traffic.cc
  LIBRARIES_TO_LINK scratch_common "${ns3-libs}" "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/common/
)
