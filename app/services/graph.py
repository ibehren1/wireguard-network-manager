# Copyright © 2026 Isaac Behrens. All rights reserved.

import ipaddress

from bson import ObjectId

from app.extensions import get_db

HOST_COLOR = "#0d6efd"
CLIENT_COLOR = "#198754"
NETWORK_COLOR = "#6c757d"
MEMBERSHIP_COLOR = "#adb5bd"


def _host_node(host, level):
    return {
        "id": f"host:{host['_id']}",
        "label": host["name"],
        "shape": "box",
        "color": HOST_COLOR,
        "font": {"color": "#fff"},
        "category": "host",
        "level": level,
    }


def _client_node(client, level):
    return {
        "id": f"client:{client['_id']}",
        "label": client["name"],
        "shape": "ellipse",
        "color": CLIENT_COLOR,
        "font": {"color": "#fff"},
        "category": "client",
        "level": level,
    }


def _network_node(network, level):
    return {
        "id": f"net:{network['_id']}",
        "label": network["cidr"],
        "title": network["name"],
        "shape": "box",
        "color": NETWORK_COLOR,
        "font": {"color": "#fff", "size": 16},
        "margin": 14,
        "widthConstraint": {"minimum": 170, "maximum": 170},
        "category": "network",
        "level": level,
    }


def _membership_edge(member_node_id, network_id, ip):
    return {
        "from": member_node_id,
        "to": f"net:{network_id}",
        "dashes": True,
        "color": {"color": MEMBERSHIP_COLOR},
        "label": ip,
        "arrows": {"to": {"enabled": False}},
    }


def _connection_edge(client_id, network_id, allowed_ips):
    """A Client's connection to a Host, drawn to the shared Network rather
    than straight to the Host — the Host already has its own membership edge
    to that same Network, so this keeps every relationship routed through
    Network nodes instead of a direct client-to-host line that would
    otherwise skip past (and often visually cross) the Network node sitting
    between them."""
    return {
        "from": f"client:{client_id}",
        "to": f"net:{network_id}",
        "color": {"color": CLIENT_COLOR},
        "arrows": {"to": {"enabled": True}},
        "label": allowed_ips,
    }


def _peer_edges(host_a_id, host_b_id, network_id, allowed_ips):
    return [
        {
            "from": f"host:{host_a_id}",
            "to": f"net:{network_id}",
            "color": {"color": HOST_COLOR},
            "arrows": {"to": {"enabled": True}},
            "label": allowed_ips,
        },
        {
            "from": f"net:{network_id}",
            "to": f"host:{host_b_id}",
            "color": {"color": HOST_COLOR},
            "arrows": {"to": {"enabled": True}},
        },
    ]


def _hierarchy_edge(parent_node_id, child_node_id):
    return {
        "from": parent_node_id,
        "to": child_node_id,
        "color": {"color": NETWORK_COLOR},
        "arrows": {"to": {"enabled": False}},
        "width": 2,
    }


def _network_parent_edge(child_id, parent_id):
    return _hierarchy_edge(f"net:{parent_id}", f"net:{child_id}")


def _resolve_allowed_ips(allowed_ips_by_id, allowed_ips_set_id):
    aset = allowed_ips_by_id.get(str(allowed_ips_set_id)) if allowed_ips_set_id else None
    return aset["cidrs"] if aset else ""


def _find_membership(memberships, network_id):
    return next((m for m in memberships if m["network_id"] == network_id), None)


def _dedupe_nodes(nodes):
    by_id = {}
    for node in nodes:
        by_id[node["id"]] = node
    return list(by_id.values())


def _network_hierarchy(networks):
    """Return (levels, parents) dicts keyed by str(network _id), based on CIDR
    containment among the given networks only. level 0 = no containing network
    in this set; each level of nesting adds 1. parent = the most specific
    (highest prefix length) containing network, for drawing a supernet->subnet
    edge."""
    parsed = []
    for net in networks:
        try:
            parsed.append((net, ipaddress.ip_network(net["cidr"], strict=True)))
        except ValueError:
            continue

    levels = {}
    parents = {}
    for net, cidr in parsed:
        containers = [(onet, ocidr) for onet, ocidr in parsed if ocidr != cidr and cidr.subnet_of(ocidr)]
        levels[str(net["_id"])] = len(containers)
        if containers:
            parent_net, _ = max(containers, key=lambda pair: pair[1].prefixlen)
            parents[str(net["_id"])] = str(parent_net["_id"])
    return levels, parents


def _member_level(network_ids, network_levels):
    """A Host/Client sits one level below the network it's attached to. When
    attached to networks at different levels, it sits between them rather
    than below the deepest one."""
    levels = [network_levels[nid] for nid in network_ids if nid in network_levels]
    if not levels:
        return 1
    lo, hi = min(levels), max(levels)
    if lo == hi:
        return lo + 1
    return (lo + hi) / 2


def _ipam_ids_with_only_p2p_descendants(networks):
    """IPAM-type network _ids whose full set of CIDR-contained descendant
    networks (any depth, any type) is non-empty and consists entirely of
    p2p-type networks. These are left out of the global topology diagram
    entirely (the dashboard graph) — an IPAM block that exists only to
    organize P2P links is noise there, since P2P networks are drawn
    separately (horizontally between the Hosts they link), not as part of
    the IPAM tree.

    Simplification: this checks every descendant regardless of type, so an
    IPAM block containing only a *nested IPAM block* that in turn contains
    only P2P networks is NOT excluded (the nested IPAM block itself doesn't
    count as "a P2P network") — an edge case not worth the extra complexity
    unless it comes up in practice.
    """
    parsed = []
    for net in networks:
        try:
            parsed.append((net, ipaddress.ip_network(net["cidr"], strict=True)))
        except ValueError:
            continue

    excluded = set()
    for net, cidr in parsed:
        if net.get("network_type", "ipam") != "ipam":
            continue
        descendants = [(onet, ocidr) for onet, ocidr in parsed if ocidr != cidr and ocidr.subnet_of(cidr)]
        if descendants and all(onet.get("network_type") == "p2p" for onet, _ in descendants):
            excluded.add(str(net["_id"]))
    return excluded


def _most_specific_ipam_ancestor(network_doc, ipam_networks):
    """The most specific `ipam`-type network (from `ipam_networks`) that
    CIDR-contains `network_doc`, or None. Used to draw a connecting line from
    a Host up to the CIDR-space tree, since Hosts have no membership on `ipam`
    networks directly (only `p2p`/`host_network`)."""
    try:
        cidr = ipaddress.ip_network(network_doc["cidr"], strict=True)
    except (ValueError, KeyError):
        return None
    best, best_prefix = None, -1
    for inet in ipam_networks:
        try:
            icidr = ipaddress.ip_network(inet["cidr"], strict=True)
        except ValueError:
            continue
        if icidr != cidr and cidr.subnet_of(icidr) and icidr.prefixlen > best_prefix:
            best, best_prefix = inet, icidr.prefixlen
    return str(best["_id"]) if best else None


def build_full_graph():
    """The dashboard's global topology graph. Fixed vertical tiers, top to
    bottom: IPAM CIDR-space networks (their own tree, nested by CIDR
    containment) -> Hosts (with P2P networks sharing that same row,
    positioned between the two Hosts they link) -> Host Networks (one flat
    row) -> Clients. Hosts/Host Networks/Clients don't derive their level
    from CIDR containment (Hosts in particular have no CIDR relationship to
    IPAM blocks at all) — the tiers are a fixed visual convention, unlike
    the dynamic per-member levels used in build_host_graph/build_client_graph.
    Every rendered IPAM network connects to its most specific IPAM parent
    (existing supernet->subnet edges) and to every Host whose p2p/host_network
    membership it CIDR-contains, so the CIDR-space tier is never floating
    disconnected from the rest of the graph.
    """
    db = get_db()
    nodes = []
    edges = []
    seen_peer_pairs = set()

    hosts = list(db.hosts.find())
    host_ids = {str(h["_id"]) for h in hosts}
    networks = list(db.networks.find())
    network_by_id = {str(n["_id"]): n for n in networks}
    allowed_ips_by_id = {str(a["_id"]): a for a in db.allowed_ips.find()}

    excluded_ipam_ids = _ipam_ids_with_only_p2p_descendants(networks)
    ipam_networks = [
        n for n in networks
        if n.get("network_type", "ipam") == "ipam" and str(n["_id"]) not in excluded_ipam_ids
    ]
    p2p_networks = [n for n in networks if n.get("network_type") == "p2p"]
    hostnet_networks = [n for n in networks if n.get("network_type") == "host_network"]

    # Every Host that's a member of a given P2P network, in node-id form — the
    # front end uses this to snap the P2P network node to the horizontal
    # midpoint of its member Hosts after the hierarchical layout runs (same-
    # level edges aren't used by vis-network's layout to influence ordering,
    # so "between the two Hosts" has to be enforced explicitly client-side).
    p2p_member_hosts = {}
    for host in hosts:
        for m in host.get("network_memberships", []):
            net = network_by_id.get(m["network_id"])
            if net and net.get("network_type") == "p2p":
                p2p_member_hosts.setdefault(m["network_id"], []).append(f"host:{host['_id']}")

    ipam_levels, ipam_parents = _network_hierarchy(ipam_networks)
    host_level = max(ipam_levels.values(), default=-1) + 1
    hostnet_level = host_level + 1
    client_level = hostnet_level + 1

    for net in ipam_networks:
        nid = str(net["_id"])
        nodes.append(_network_node(net, ipam_levels.get(nid, 0)))
        parent_id = ipam_parents.get(nid)
        if parent_id:
            edges.append(_network_parent_edge(nid, parent_id))

    for net in p2p_networks:
        node = _network_node(net, host_level)
        node["member_host_ids"] = p2p_member_hosts.get(str(net["_id"]), [])
        nodes.append(node)

    for net in hostnet_networks:
        nodes.append(_network_node(net, hostnet_level))

    for host in hosts:
        hid = str(host["_id"])
        memberships = host.get("network_memberships", [])
        nodes.append(_host_node(host, host_level))

        # Connect this Host up to the CIDR-space tree: the most specific
        # surviving `ipam` network that contains any of this Host's `p2p`/
        # `host_network` memberships (Hosts have no membership on `ipam`
        # networks directly, so this is the only way they link to that tier).
        ancestor_ids = set()
        for m in memberships:
            net = network_by_id.get(m["network_id"])
            if net and net.get("network_type") in ("p2p", "host_network"):
                ancestor_id = _most_specific_ipam_ancestor(net, ipam_networks)
                if ancestor_id:
                    ancestor_ids.add(ancestor_id)
        for ancestor_id in ancestor_ids:
            edges.append(_hierarchy_edge(f"net:{ancestor_id}", f"host:{hid}"))

        for m in memberships:
            edges.append(_membership_edge(f"host:{hid}", m["network_id"], m["ip"]))
        for pconn in host.get("peer_connections", []):
            if pconn["peer_host_id"] not in host_ids:
                continue
            pair_key = frozenset([hid, pconn["peer_host_id"], pconn["network_id"]])
            if pair_key in seen_peer_pairs:
                continue
            seen_peer_pairs.add(pair_key)
            allowed = _resolve_allowed_ips(allowed_ips_by_id, pconn.get("allowed_ips_set_id"))
            edges.extend(_peer_edges(hid, pconn["peer_host_id"], pconn["network_id"], allowed))

    for client in db.clients.find():
        cid = str(client["_id"])
        memberships = client.get("network_memberships", [])
        nodes.append(_client_node(client, client_level))
        for m in memberships:
            edges.append(_membership_edge(f"client:{cid}", m["network_id"], m["ip"]))
        for conn in client.get("connections", []):
            if conn["host_id"] not in host_ids:
                continue
            allowed = _resolve_allowed_ips(allowed_ips_by_id, conn.get("allowed_ips_set_id"))
            edges.append(_connection_edge(cid, conn["network_id"], allowed))

    return {"nodes": _dedupe_nodes(nodes), "edges": edges}


def build_host_graph(host_id):
    db = get_db()
    host = db.hosts.find_one({"_id": ObjectId(host_id)})
    if not host:
        return {"nodes": [], "edges": []}

    hid = str(host["_id"])
    allowed_ips_by_id = {str(a["_id"]): a for a in db.allowed_ips.find()}

    memberships = host.get("network_memberships", [])
    host_network_ids = {m["network_id"] for m in memberships}
    peer_connections = host.get("peer_connections", [])

    # A network is "host-to-host" if it's the network_id of at least one of this
    # host's peer connections (a P2P link runs over it). Everything else the host
    # belongs to is a "regular" network (LAN-style, with Clients on it).
    host_to_host_network_ids = {
        pconn["network_id"] for pconn in peer_connections if pconn["network_id"] in host_network_ids
    }
    regular_network_ids = host_network_ids - host_to_host_network_ids

    network_docs = []
    for m in memberships:
        net = db.networks.find_one({"_id": ObjectId(m["network_id"])})
        if net:
            network_docs.append(net)
    hth_network_docs = [n for n in network_docs if str(n["_id"]) in host_to_host_network_ids]
    regular_network_docs = [n for n in network_docs if str(n["_id"]) in regular_network_ids]

    # Hosts + host-to-host networks all sit at level 0. Regular networks are
    # nested below the host by CIDR-containment depth among themselves, shifted
    # up by 1 so the shallowest regular network lands at level 1.
    regular_levels_raw, regular_parents = _network_hierarchy(regular_network_docs)
    regular_levels = {nid: level + 1 for nid, level in regular_levels_raw.items()}

    nodes = [_host_node(host, 0)]
    edges = []

    for net in hth_network_docs:
        nodes.append(_network_node(net, 0))
    for net in regular_network_docs:
        nid = str(net["_id"])
        nodes.append(_network_node(net, regular_levels.get(nid, 1)))
        parent_id = regular_parents.get(nid)
        if parent_id:
            edges.append(_network_parent_edge(nid, parent_id))
    for m in memberships:
        edges.append(_membership_edge(f"host:{hid}", m["network_id"], m["ip"]))

    for client in db.clients.find({"connections.host_id": hid}):
        matching_conns = [conn for conn in client.get("connections", []) if conn["host_id"] == hid]
        # Sits one level below whichever regular network its connection(s) to
        # this host run over. Falls back to level 1 (just below the hosts) if
        # none of those connections' networks are classified as regular for
        # this host — e.g. tied in via a network classified as host-to-host,
        # an unusual edge case.
        conn_network_ids = [conn["network_id"] for conn in matching_conns]
        client_level = _member_level(conn_network_ids, regular_levels)
        for conn in matching_conns:
            nodes.append(_client_node(client, client_level))
            allowed = _resolve_allowed_ips(allowed_ips_by_id, conn.get("allowed_ips_set_id"))
            edges.append(_connection_edge(str(client["_id"]), conn["network_id"], allowed))

    for pconn in peer_connections:
        peer = db.hosts.find_one({"_id": ObjectId(pconn["peer_host_id"])})
        if peer:
            nodes.append(_host_node(peer, 0))
            allowed = _resolve_allowed_ips(allowed_ips_by_id, pconn.get("allowed_ips_set_id"))
            edges.extend(_peer_edges(hid, pconn["peer_host_id"], pconn["network_id"], allowed))

    return {"nodes": _dedupe_nodes(nodes), "edges": edges}


def build_client_graph(client_id):
    db = get_db()
    client = db.clients.find_one({"_id": ObjectId(client_id)})
    if not client:
        return {"nodes": [], "edges": []}

    cid = str(client["_id"])
    allowed_ips_by_id = {str(a["_id"]): a for a in db.allowed_ips.find()}

    memberships = client.get("network_memberships", [])
    network_docs = []
    for m in memberships:
        net = db.networks.find_one({"_id": ObjectId(m["network_id"])})
        if net:
            network_docs.append(net)
    # Hosts the Client connects to sit at the top (level 0). The Client's own
    # networks sit below the hosts (level 1, or deeper for nested CIDR
    # hierarchy among them), and the Client sits one level below its
    # network(s).
    network_levels_raw, network_parents = _network_hierarchy(network_docs)
    network_levels = {nid: level + 1 for nid, level in network_levels_raw.items()}

    client_level = _member_level([m["network_id"] for m in memberships], network_levels)
    nodes = [_client_node(client, client_level)]
    edges = []

    for net in network_docs:
        nid = str(net["_id"])
        nodes.append(_network_node(net, network_levels.get(nid, 1)))
        parent_id = network_parents.get(nid)
        if parent_id:
            edges.append(_network_parent_edge(nid, parent_id))
    for m in memberships:
        edges.append(_membership_edge(f"client:{cid}", m["network_id"], m["ip"]))

    for conn in client.get("connections", []):
        host = db.hosts.find_one({"_id": ObjectId(conn["host_id"])})
        if host:
            nodes.append(_host_node(host, 0))
            # The connection edge below now points at the shared Network, not
            # the Host directly (see _connection_edge) — without this, the
            # Host node would be left with no edge at all in this graph, since
            # build_client_graph (unlike build_full_graph/build_host_graph)
            # doesn't otherwise draw the Host's own membership on this
            # Network. Mirror that membership edge here so the Host stays
            # connected to the rest of the graph.
            host_membership = _find_membership(host.get("network_memberships", []), conn["network_id"])
            if host_membership:
                edges.append(_membership_edge(f"host:{conn['host_id']}", conn["network_id"], host_membership["ip"]))
            allowed = _resolve_allowed_ips(allowed_ips_by_id, conn.get("allowed_ips_set_id"))
            edges.append(_connection_edge(cid, conn["network_id"], allowed))

    return {"nodes": _dedupe_nodes(nodes), "edges": edges}
