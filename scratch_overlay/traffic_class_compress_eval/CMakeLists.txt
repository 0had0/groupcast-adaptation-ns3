# scratch/traffic_class_compress_eval/CMakeLists.txt
#
# Adaptive application-layer compression overlay on top of traffic_class_eval.
# All shared modules live in scratch_common (scratch_common links zstd/lz4 when
# found, and sets KPI_HOOKS_HAVE_CODEC_LIBS=1 for its own kpi_hooks.cc).
#
# compression_controller.cc is specific to this sim and compiled here.
# zstd/lz4 are also needed by compression_controller.cc directly.

find_library(ZSTD_LIB zstd PATHS /opt/local/lib /opt/homebrew/lib /usr/local/lib REQUIRED)
find_library(LZ4_LIB  lz4  PATHS /opt/local/lib /opt/homebrew/lib /usr/local/lib REQUIRED)
find_path(ZSTD_INCLUDE_DIR zstd.h PATHS /opt/local/include /opt/homebrew/include /usr/local/include)
find_path(LZ4_INCLUDE_DIR  lz4.h  PATHS /opt/local/include /opt/homebrew/include /usr/local/include)

include_directories(${ZSTD_INCLUDE_DIR} ${LZ4_INCLUDE_DIR})
# Include this scratch dir so compression_tag.h resolves via __has_include.
include_directories(${CMAKE_CURRENT_SOURCE_DIR})

build_exec(
  EXECNAME traffic_class_compress_eval
  EXECNAME_PREFIX scratch_traffic_class_compress_eval_
  SOURCE_FILES
    traffic_class_compress_eval.cc
    compression_controller.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
    ${ZSTD_LIB}
    ${LZ4_LIB}
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/traffic_class_compress_eval/
)

# Mark KPI_HOOKS_HAVE_CODEC_LIBS for the sim-local sources as well.
target_compile_definitions(
  scratch_traffic_class_compress_eval_traffic_class_compress_eval
  PRIVATE KPI_HOOKS_HAVE_CODEC_LIBS=1
)
