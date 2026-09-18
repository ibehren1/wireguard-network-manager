# Copyright © 2026 Isaac Behrens. All rights reserved.

import ipaddress


def parse_network(cidr):
    """Raises ValueError if cidr is not a valid network address (e.g. 10.0.0.5/24)."""
    return ipaddress.ip_network(cidr, strict=True)


def next_free_ip(cidr, used_ips):
    network = ipaddress.ip_network(cidr, strict=True)
    used = set(used_ips)
    for candidate in network.hosts():
        candidate_str = str(candidate)
        if candidate_str not in used:
            return candidate_str
    return None


def ip_in_network(ip, cidr):
    network = ipaddress.ip_network(cidr, strict=True)
    try:
        return ipaddress.ip_address(ip) in network
    except ValueError:
        return False


def prefixlen(cidr):
    return ipaddress.ip_network(cidr, strict=True).prefixlen
