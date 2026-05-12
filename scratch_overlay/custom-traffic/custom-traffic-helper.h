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

#ifndef CUSTOM_TRAFFIC_HELPER_H
#define CUSTOM_TRAFFIC_HELPER_H

#include "ns3/application-helper.h"
#include "ns3/address.h"

#include <stdint.h>

namespace ns3
{

class CustomTrafficHelper : public ApplicationHelper
{
  public:
    CustomTrafficHelper(const std::string& protocol, const Address& address);

    void SetTrafficMode(int mode);
};

} // namespace ns3

#endif /* CUSTOM_TRAFFIC_HELPER_H */
