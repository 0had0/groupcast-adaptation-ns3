/*
 * Copyright (c) 2026
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation;
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 */

#include "custom-traffic-helper.h"

#include "custom-traffic-application.h"

#include "ns3/enum.h"
#include "ns3/string.h"
#include "ns3/uinteger.h"

namespace ns3
{

CustomTrafficHelper::CustomTrafficHelper(const std::string& protocol, const Address& address)
    : ApplicationHelper("ns3::CustomTrafficApplication")
{
    m_factory.Set("Protocol", StringValue(protocol));
    m_factory.Set("Remote", AddressValue(address));
}

void
CustomTrafficHelper::SetTrafficMode(int mode)
{
    m_factory.Set("TrafficMode", EnumValue(static_cast<CustomTrafficApplication::TrafficMode>(mode)));
}

} // namespace ns3
