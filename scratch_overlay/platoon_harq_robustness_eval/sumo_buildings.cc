// sumo_buildings.cc — see sumo_buildings.h

#include "sumo_buildings.h"

#include "ns3/box.h"
#include "ns3/building.h"
#include "ns3/building-list.h"
#include "ns3/log.h"

#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <vector>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("SumoBuildings");

namespace
{

std::string
ReadAll(const std::string& path, bool& ok)
{
    std::ifstream f(path);
    if (!f.is_open())
    {
        ok = false;
        return {};
    }
    std::ostringstream ss;
    ss << f.rdbuf();
    ok = true;
    return ss.str();
}

// Extract the value of attribute `attr` from the tag starting at `pos`.
std::string
Attr(const std::string& s, size_t pos, const std::string& attr)
{
    const std::string key = attr + "=\"";
    size_t p = s.find(key, pos);
    if (p == std::string::npos)
    {
        return {};
    }
    p += key.size();
    size_t q = s.find('"', p);
    if (q == std::string::npos)
    {
        return {};
    }
    return s.substr(p, q - p);
}

// Parse SUMO's "x,y x,y x,y" polygon shape into vertex pairs.
std::vector<std::pair<double, double>>
ParseShape(const std::string& shape)
{
    std::vector<std::pair<double, double>> out;
    std::istringstream ss(shape);
    std::string tok;
    while (ss >> tok)
    {
        size_t c = tok.find(',');
        if (c == std::string::npos)
        {
            continue;
        }
        try
        {
            out.emplace_back(std::stod(tok.substr(0, c)), std::stod(tok.substr(c + 1)));
        }
        catch (...)
        {
            // ignore malformed vertex
        }
    }
    return out;
}

} // namespace

std::string
SumoNetProjParameter(const std::string& netXml)
{
    bool ok = false;
    const std::string s = ReadAll(netXml, ok);
    if (!ok)
    {
        return {};
    }
    // <location netOffset="0,0" convBoundary="…" origBoundary="…" projParameter="!"/>
    const size_t loc = s.find("<location");
    if (loc == std::string::npos)
    {
        return {};
    }
    std::string p = Attr(s, loc, "projParameter");
    if (p == "!")
    {
        p.clear();
    }
    return p;
}

uint32_t
InstallSumoBuildings(const std::string& netXml, double heightM)
{
    bool ok = false;
    const std::string s = ReadAll(netXml, ok);
    if (!ok)
    {
        NS_LOG_ERROR("cannot read SUMO net file " << netXml);
        return 0;
    }

    const std::string proj = SumoNetProjParameter(netXml);
    if (!proj.empty())
    {
        NS_LOG_ERROR("SUMO net file uses projParameter='" << proj
                                                  << "': SUMO metres are not ns-3 metres. "
                                                  << "Refusing to install buildings rather than "
                                                     "mis-placing them.");
        return 0;
    }

    uint32_t added = 0;
    uint32_t nonRect = 0;

    size_t pos = 0;
    while (true)
    {
        const size_t t = s.find("<type id=\"building", pos);
        if (t == std::string::npos)
        {
            break;
        }
        const size_t typeEnd = s.find("</type>", t);
        if (typeEnd == std::string::npos)
        {
            break;
        }
        const size_t poly = s.find("<polygon", t);
        if (poly != std::string::npos && poly < typeEnd)
        {
            const std::string shape = Attr(s, poly, "shape");
            const auto verts = ParseShape(shape);
            if (verts.size() >= 3)
            {
                double xmin = verts[0].first, xmax = verts[0].first;
                double ymin = verts[0].second, ymax = verts[0].second;
                for (const auto& v : verts)
                {
                    xmin = std::min(xmin, v.first);
                    xmax = std::max(xmax, v.first);
                    ymin = std::min(ymin, v.second);
                    ymax = std::max(ymax, v.second);
                }
                // Exact only for an axis-aligned rectangle; warn otherwise so
                // the approximation is never silent.
                std::vector<double> xs, ys;
                for (const auto& v : verts)
                {
                    xs.push_back(v.first);
                    ys.push_back(v.second);
                }
                std::sort(xs.begin(), xs.end());
                std::sort(ys.begin(), ys.end());
                xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
                ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
                if (xs.size() > 2 || ys.size() > 2)
                {
                    ++nonRect;
                }

                Ptr<Building> b = CreateObject<Building>();
                b->SetBoundaries(Box(xmin, xmax, ymin, ymax, 0.0, heightM));
                b->SetNFloors(1);
                b->SetNRoomsX(1);
                b->SetNRoomsY(1);
                // The constructor already registered it in BuildingList.
                ++added;
            }
        }
        pos = typeEnd + 7;
    }

    NS_LOG_INFO("registered " << added << " SUMO buildings from " << netXml
                              << " (height " << heightM << " m)");
    if (nonRect)
    {
        NS_LOG_WARN(nonRect
                    << " building polygon(s) were not axis-aligned rectangles; the bounding "
                       "box is an approximation for those");
    }
    return added;
}

} // namespace ns3