# Grid'5000 environment of the genie campaign (sourced by every g5k script).
# Layout on the site: ~/groupcast-adaptation-ns3/{ns-3-dev, scratch_overlay}; build dependencies missing from the
# standard environment (GSL, SQLite and Eigen headers, GSL runtime) unpacked from Debian packages into ~/g5k_deps/root
# (apt-get download + dpkg -x, no root); ninja from pip --user.
export G5K_REPO="${G5K_REPO:-$HOME/groupcast-adaptation-ns3}"
export G5K_DEPS="${G5K_DEPS:-$HOME/g5k_deps/root}"
export G5K_STUDY="$G5K_REPO/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study"
export PATH="$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="$G5K_DEPS/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export G5K_OK="$G5K_STUDY/out/g5k_preflight_ok"   # written by setup.sh when build + preflight passed on this cluster
