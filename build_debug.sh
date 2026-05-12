ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3="$ROOT/ns-3-dev"

${NS3}/ns3 configure -d debug --enable-tests --enable-examples --disable-werror 
${NS3}/ns3 build -j 8
