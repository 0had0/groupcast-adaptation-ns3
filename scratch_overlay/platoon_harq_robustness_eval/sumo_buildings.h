// sumo_buildings.h
//
// Registers the building footprints that `joint-coop-harq-mac/sumo-exports`
// injects into the SUMO network as netconvert `<type id="building.N">`
// polygons, so that ns-3's BuildingsChannelConditionModel can actually see
// them.
//
// WHY THIS IS NEEDED
// nr_sl_stack.cc calls `BuildingsHelper::Install(nodes)`, but without a
// building map that call only attaches an empty MobilityBuildingInfo. SUMO's
// building polygons live inside the `.net.xml` and are never loaded by ns-3,
// so `BuildingList` stays empty, `IsLineOfSightBlocked()` always returns
// false, and `ThreeGppV2vUrbanChannelConditionModel::ComputePnlos()` always
// returns 0. That makes the **NLOS** branch of the channel condition
// unreachable: every link is forced to either LOS or NLOSv. With the
// buildings registered, pNlos becomes 1 whenever a building blocks the link
// and the simulator can report all three conditions (LOS / NLOSv / NLOS).
//
// COORDINATES
// roundabout.net.xml has `projParameter="!"` and
// `origBoundary == convBoundary`, i.e. SUMO performed no geo projection, so
// the metres in the net file are the same metres the traces are recorded in
// and can be handed to ns-3 unchanged.
//
// GEOMETRY
// ns-3's Building model is a single axis-aligned Box tested with a 3-D
// Separating Axis Test (`Box::IsIntersect`). All 98 SUMO polygons in this
// scenario are already axis-aligned rectangles, so the bounding box of each
// polygon is exact rather than an approximation. The loader warns if it ever
// meets a polygon that is not a rectangle.

#ifndef SUMO_BUILDINGS_H
#define SUMO_BUILDINGS_H

#include <cstdint>
#include <string>

namespace ns3
{

/**
 * Parse the `<type id="building.N"> … <polygon shape="x,y x,y …"/> … </type>`
 * entries of a SUMO .net.xml and register each one in ns3::BuildingList.
 *
 * @param netXml   path to the SUMO .net.xml
 * @param heightM  building height in metres. SUMO carries no height for these
 *                 decorative blocks, so this is a modelling choice; any value
 *                 comfortably above the 1.5 m antenna height blocks the link.
 * @return the number of buildings registered
 */
uint32_t InstallSumoBuildings(const std::string& netXml, double heightM);

/**
 * True when the .net.xml declares a projection (projParameter != "!"), i.e.
 * when SUMO metres are NOT ns-3 metres and a transformation would be needed.
 * Returns the projParameter string (empty when absent or "!"), so the caller
 * can refuse to install rather than silently mis-place every building.
 */
std::string SumoNetProjParameter(const std::string& netXml);

} // namespace ns3

#endif // SUMO_BUILDINGS_H