# scratch/sl_bler_sweep/CMakeLists.txt
#
# Single NR 5G Sidelink groupcast link — BLER vs SNR sweep.
# sl_stack_bler.cc provides BuildSlStackBler() with channel-condition control.
# All shared modules (geometric channel, groupcast flows, custom traffic) are
# in scratch_common.

build_exec(
  EXECNAME sl_bler_sweep
  EXECNAME_PREFIX scratch_sl_bler_sweep_
  SOURCE_FILES
    sl_bler_sweep.cc
    sl_stack_bler.cc
  LIBRARIES_TO_LINK
    scratch_common
    "${ns3-libs}"
    "${ns3-contrib-libs}"
  EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/sl_bler_sweep/
)
