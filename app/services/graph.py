from bson import ObjectId

from app.extensions import get_db

HOST_COLOR = "#0d6efd"
CLIENT_COLOR = "#198754"
NETWORK_COLOR = "#6c757d"
MEMBERSHIP_COLOR = "#adb5bd"


def _host_node(host):
    return {
        "id": f"host:{host['_id']}",
        "label": host["name"],
        "shape": "box",
        "color": HOST_COLOR,
        "font": {"color": "#fff"},
    }


def _client_node(client):
    return {
        "id": f"client:{client['_id']}",
        "label": client["name"],
        "shape": "ellipse",
        "color": CLIENT_COLOR,
        "font": {"color": "#fff"},
    }


def _network_node(network):
    return {
        "id": f"net:{network['_id']}",
        "label": network["name"],
        "shape": "diamond",
        "color": NETWORK_COLOR,
        "font": {"color": "#fff"},
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


def _dedupe_nodes(nodes):
    by_id = {}
    for node in nodes:
        by_id[node["id"]] = node
    return list(by_id.values())


def build_full_graph():
    db = get_db()
    nodes = []
    edges = []
    seen_peer_pairs = set()

    hosts = list(db.hosts.find())
    host_ids = {str(h["_id"]) for h in hosts}

    for net in db.networks.find():
        nodes.append(_network_node(net))

    for host in hosts:
        hid = str(host["_id"])
        nodes.append(_host_node(host))
        for m in host.get("network_memberships", []):
            edges.append(_membership_edge(f"host:{hid}", m["network_id"], m["ip"]))
        for pconn in host.get("peer_connections", []):
            if pconn["peer_host_id"] not in host_ids:
                continue
            pair_key = frozenset([hid, pconn["peer_host_id"], pconn["network_id"]])
            if pair_key in seen_peer_pairs:
                continue
            seen_peer_pairs.add(pair_key)
            edges.append(_peer_edge(hid, pconn["peer_host_id"], pconn.get("allowed_ips", "")))

    for client in db.clients.find():
        cid = str(client["_id"])
        nodes.append(_client_node(client))
        for m in client.get("network_memberships", []):
            edges.append(_membership_edge(f"client:{cid}", m["network_id"], m["ip"]))
        for conn in client.get("connections", []):
            if conn["host_id"] not in host_ids:
                continue
            edges.append(_connection_edge(cid, conn["host_id"], conn.get("allowed_ips", "")))

    return {"nodes": _dedupe_nodes(nodes), "edges": edges}


def build_host_graph(host_id):
    db = get_db()
    host = db.hosts.find_one({"_id": ObjectId(host_id)})
    if not host:
        return {"nodes": [], "edges": []}

    hid = str(host["_id"])
    nodes = [_host_node(host)]
    edges = []

    for m in host.get("network_memberships", []):
        net = db.networks.find_one({"_id": ObjectId(m["network_id"])})
        if not net:
            continue
        nodes.append(_network_node(net))
        edges.append(_membership_edge(f"host:{hid}", m["network_id"], m["ip"]))

    for client in db.clients.find({"connections.host_id": hid}):
        for conn in client.get("connections", []):
            if conn["host_id"] == hid:
                nodes.append(_client_node(client))
                edges.append(_connection_edge(str(client["_id"]), hid, conn.get("allowed_ips", "")))

    for pconn in host.get("peer_connections", []):
        peer = db.hosts.find_one({"_id": ObjectId(pconn["peer_host_id"])})
        if peer:
            nodes.append(_host_node(peer))
            edges.append(_peer_edge(hid, pconn["peer_host_id"], pconn.get("allowed_ips", "")))

    return {"nodes": _dedupe_nodes(nodes), "edges": edges}


def build_client_graph(client_id):
    db = get_db()
    client = db.clients.find_one({"_id": ObjectId(client_id)})
    if not client:
        return {"nodes": [], "edges": []}

    cid = str(client["_id"])
    nodes = [_client_node(client)]
    edges = []

    for m in client.get("network_memberships", []):
        net = db.networks.find_one({"_id": ObjectId(m["network_id"])})
        if not net:
            continue
        nodes.append(_network_node(net))
        edges.append(_membership_edge(f"client:{cid}", m["network_id"], m["ip"]))

    for conn in client.get("connections", []):
        host = db.hosts.find_one({"_id": ObjectId(conn["host_id"])})
        if host:
            nodes.append(_host_node(host))
            edges.append(_connection_edge(cid, conn["host_id"], conn.get("allowed_ips", "")))

    return {"nodes": _dedupe_nodes(nodes), "edges": edges}
