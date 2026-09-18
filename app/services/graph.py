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
        "label": f"{network['name']}\n{network['cidr']}",
        "shape": "database",
        "color": NETWORK_COLOR,
        "font": {"color": "#fff", "size": 16},
        "margin": 14,
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


def _connection_edge(client_id, host_id, allowed_ips):
    return {
        "from": f"client:{client_id}",
        "to": f"host:{host_id}",
        "color": {"color": CLIENT_COLOR},
        "arrows": {"to": {"enabled": True}},
        "label": allowed_ips,
    }


def _peer_edge(host_a_id, host_b_id, allowed_ips):
    return {
        "from": f"host:{host_a_id}",
        "to": f"host:{host_b_id}",
        "color": {"color": HOST_COLOR},
        "arrows": {"to": {"enabled": True}, "from": {"enabled": True}},
        "label": allowed_ips,
    }


def _network_parent_edge(child_id, parent_id):
    return {
        "from": f"net:{parent_id}",
        "to": f"net:{child_id}",
        "color": {"color": NETWORK_COLOR},
        "arrows": {"to": {"enabled": False}},
        "width": 2,
    }


def _resolve_allowed_ips(allowed_ips_by_id, allowed_ips_set_id):
    aset = allowed_ips_by_id.get(str(allowed_ips_set_id)) if allowed_ips_set_id else None
    return aset["cidrs"] if aset else ""


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


def build_full_graph():
    db = get_db()
    nodes = []
    edges = []
    seen_peer_pairs = set()

    hosts = list(db.hosts.find())
    host_ids = {str(h["_id"]) for h in hosts}
    networks = list(db.networks.find())
    network_levels, network_parents = _network_hierarchy(networks)
    allowed_ips_by_id = {str(a["_id"]): a for a in db.allowed_ips.find()}

    for net in networks:
        nid = str(net["_id"])
        nodes.append(_network_node(net, network_levels.get(nid, 0)))
        parent_id = network_parents.get(nid)
        if parent_id:
            edges.append(_network_parent_edge(nid, parent_id))

    for host in hosts:
        hid = str(host["_id"])
        memberships = host.get("network_memberships", [])
        level = _member_level([m["network_id"] for m in memberships], network_levels)
        nodes.append(_host_node(host, level))
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
            edges.append(_peer_edge(hid, pconn["peer_host_id"], allowed))

    for client in db.clients.find():
        cid = str(client["_id"])
        memberships = client.get("network_memberships", [])
        level = _member_level([m["network_id"] for m in memberships], network_levels)
        nodes.append(_client_node(client, level))
        for m in memberships:
            edges.append(_membership_edge(f"client:{cid}", m["network_id"], m["ip"]))
        for conn in client.get("connections", []):
            if conn["host_id"] not in host_ids:
                continue
            allowed = _resolve_allowed_ips(allowed_ips_by_id, conn.get("allowed_ips_set_id"))
            edges.append(_connection_edge(cid, conn["host_id"], allowed))

    return {"nodes": _dedupe_nodes(nodes), "edges": edges}


def build_host_graph(host_id):
    db = get_db()
    host = db.hosts.find_one({"_id": ObjectId(host_id)})
    if not host:
        return {"nodes": [], "edges": []}

    hid = str(host["_id"])
    allowed_ips_by_id = {str(a["_id"]): a for a in db.allowed_ips.find()}

    memberships = host.get("network_memberships", [])
    network_docs = []
    for m in memberships:
        net = db.networks.find_one({"_id": ObjectId(m["network_id"])})
        if net:
            network_docs.append(net)
    network_levels, network_parents = _network_hierarchy(network_docs)

    host_level = _member_level([m["network_id"] for m in memberships], network_levels)
    nodes = [_host_node(host, host_level)]
    edges = []

    for net in network_docs:
        nid = str(net["_id"])
        nodes.append(_network_node(net, network_levels.get(nid, 0)))
        parent_id = network_parents.get(nid)
        if parent_id:
            edges.append(_network_parent_edge(nid, parent_id))
    for m in memberships:
        edges.append(_membership_edge(f"host:{hid}", m["network_id"], m["ip"]))

    for client in db.clients.find({"connections.host_id": hid}):
        for conn in client.get("connections", []):
            if conn["host_id"] == hid:
                nodes.append(_client_node(client, host_level + 1))
                allowed = _resolve_allowed_ips(allowed_ips_by_id, conn.get("allowed_ips_set_id"))
                edges.append(_connection_edge(str(client["_id"]), hid, allowed))

    for pconn in host.get("peer_connections", []):
        peer = db.hosts.find_one({"_id": ObjectId(pconn["peer_host_id"])})
        if peer:
            nodes.append(_host_node(peer, host_level))
            allowed = _resolve_allowed_ips(allowed_ips_by_id, pconn.get("allowed_ips_set_id"))
            edges.append(_peer_edge(hid, pconn["peer_host_id"], allowed))

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
    network_levels, network_parents = _network_hierarchy(network_docs)

    client_level = _member_level([m["network_id"] for m in memberships], network_levels)
    nodes = [_client_node(client, client_level)]
    edges = []

    for net in network_docs:
        nid = str(net["_id"])
        nodes.append(_network_node(net, network_levels.get(nid, 0)))
        parent_id = network_parents.get(nid)
        if parent_id:
            edges.append(_network_parent_edge(nid, parent_id))
    for m in memberships:
        edges.append(_membership_edge(f"client:{cid}", m["network_id"], m["ip"]))

    for conn in client.get("connections", []):
        host = db.hosts.find_one({"_id": ObjectId(conn["host_id"])})
        if host:
            nodes.append(_host_node(host, client_level))
            allowed = _resolve_allowed_ips(allowed_ips_by_id, conn.get("allowed_ips_set_id"))
            edges.append(_connection_edge(cid, conn["host_id"], allowed))

    return {"nodes": _dedupe_nodes(nodes), "edges": edges}
