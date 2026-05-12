# CMakeLists.txt for custom-traffic module

# Build the test executable
build_exec(
  EXECNAME test-custom-traffic
  EXECNAME_PREFIX scratch_custom-traffic_
  SOURCE_FILES
    test-custom-traffic.cc
    custom-traffic-application.cc
    custom-traffic-helper.cc
  LIBRARIES_TO_LINK "${ns3-libs}" "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch_overlay/custom-traffic/
)
