from bson import ObjectId

from app.extensions import get_db
from app.utils.crypto import decrypt_private_key


def _computed_endpoint(host):
    if host.get("hostname") and host.get("listen_port"):
        return f"{host['hostname']}:{host['listen_port']}"
    return None


def _address_lines(memberships, db):
    parts = []
    for m in memberships:
        net = db.networks.find_one({"_id": ObjectId(m["network_id"])})
        if not net:
            continue
        net_prefixlen = net["cidr"].split("/")[1]
        parts.append(f"{m['ip']}/{net_prefixlen}")
    return ", ".join(parts)


def render_host_config(host_id):
    db = get_db()
    host = db.hosts.find_one({"_id": ObjectId(host_id)})
    if not host:
        raise ValueError("Host not found")

    key = db.keys.find_one({"_id": ObjectId(host["active_key_id"])})
    private_key = decrypt_private_key(key["private_key"])

    lines = ["[Interface]", f"PrivateKey = {private_key}"]
    address = _address_lines(host.get("network_memberships", []), db)
    if address:
        lines.append(f"Address = {address}")
    if host.get("listen_port"):
        lines.append(f"ListenPort = {host['listen_port']}")
    if host.get("dns"):
        lines.append(f"DNS = {host['dns']}")
    if host.get("mtu"):
        lines.append(f"MTU = {host['mtu']}")

    for client in db.clients.find({"connections.host_id": str(host["_id"])}):
        client_key = db.keys.find_one({"_id": ObjectId(client["active_key_id"])})
        for conn in client.get("connections", []):
            if conn["host_id"] != str(host["_id"]):
                continue
            membership = next(
                (m for m in client.get("network_memberships", []) if m["network_id"] == conn["network_id"]),
                None,
            )
            if not membership:
                continue
            lines.append("")
            lines.append(f"[Peer]  # Client: {client['name']}")
            lines.append(f"PublicKey = {client_key['public_key']}")
            lines.append(f"AllowedIPs = {membership['ip']}/32")
            if conn.get("persistent_keepalive"):
                lines.append(f"PersistentKeepalive = {conn['persistent_keepalive']}")

    for pconn in host.get("peer_connections", []):
        peer_host = db.hosts.find_one({"_id": ObjectId(pconn["peer_host_id"])})
        if not peer_host:
            continue
        peer_key = db.keys.find_one({"_id": ObjectId(peer_host["active_key_id"])})
        lines.append("")
        lines.append(f"[Peer]  # Host: {peer_host['name']}")
        lines.append(f"PublicKey = {peer_key['public_key']}")
        endpoint = pconn.get("endpoint_override") or _computed_endpoint(peer_host)
        if endpoint:
            lines.append(f"Endpoint = {endpoint}")
        lines.append(f"AllowedIPs = {pconn['allowed_ips']}")
        if pconn.get("persistent_keepalive"):
            lines.append(f"PersistentKeepalive = {pconn['persistent_keepalive']}")

    return "\n".join(lines) + "\n"


def render_client_config(client_id, connection_index=None, allowed_ips_override=None):
    db = get_db()
    client = db.clients.find_one({"_id": ObjectId(client_id)})
    if not client:
        raise ValueError("Client not found")

    key = db.keys.find_one({"_id": ObjectId(client["active_key_id"])})
    private_key = decrypt_private_key(key["private_key"])

    lines = ["[Interface]", f"PrivateKey = {private_key}"]
    address = _address_lines(client.get("network_memberships", []), db)
    if address:
        lines.append(f"Address = {address}")
    if client.get("dns"):
        lines.append(f"DNS = {client['dns']}")

    connections = client.get("connections", [])
    if connection_index is not None:
        connections = [connections[connection_index]]

    for conn in connections:
        host = db.hosts.find_one({"_id": ObjectId(conn["host_id"])})
        if not host:
            continue
        host_key = db.keys.find_one({"_id": ObjectId(host["active_key_id"])})
        lines.append("")
        lines.append(f"[Peer]  # Host: {host['name']}")
        lines.append(f"PublicKey = {host_key['public_key']}")
        host_endpoint = _computed_endpoint(host)
        if host_endpoint:
            lines.append(f"Endpoint = {host_endpoint}")
        allowed = conn["allowed_ips"]
        if allowed_ips_override and connection_index is not None:
            allowed = allowed_ips_override
        lines.append(f"AllowedIPs = {allowed}")
        if conn.get("persistent_keepalive"):
            lines.append(f"PersistentKeepalive = {conn['persistent_keepalive']}")

    return "\n".join(lines) + "\n"
