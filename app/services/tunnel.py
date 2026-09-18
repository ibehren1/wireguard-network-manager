from bson import ObjectId

from app.extensions import get_db
from app.utils.crypto import decrypt_private_key


def _computed_endpoint(host):
    if host.get("hostname") and host.get("listen_port"):
        return f"{host['hostname']}:{host['listen_port']}"
    return None


def _find_membership(memberships, network_id):
    return next((m for m in memberships if m["network_id"] == network_id), None)


def _single_address(membership, db):
    net = db.networks.find_one({"_id": ObjectId(membership["network_id"])})
    if not net:
        return None
    net_prefixlen = net["cidr"].split("/")[1]
    return f"{membership['ip']}/{net_prefixlen}"


def render_host_interface_config(host_id, network_id):
    db = get_db()
    host = db.hosts.find_one({"_id": ObjectId(host_id)})
    if not host:
        raise ValueError("Host not found")

    membership = _find_membership(host.get("network_memberships", []), network_id)
    if not membership:
        raise ValueError("Host is not a member of that network")

    key = db.keys.find_one({"_id": ObjectId(host["active_key_id"])}) if host.get("active_key_id") else None
    private_key = decrypt_private_key(key["private_key"]) if key and key.get("private_key") else ""

    lines = ["[Interface]", f"PrivateKey = {private_key}"]
    address = _single_address(membership, db)
    if address:
        lines.append(f"Address = {address}")
    if host.get("listen_port"):
        lines.append(f"ListenPort = {host['listen_port']}")
    if host.get("dns_server_id"):
        dns_server = db.dns_servers.find_one({"_id": ObjectId(host["dns_server_id"])})
        if dns_server:
            lines.append(f"DNS = {dns_server['ips']}")
    if host.get("mtu"):
        lines.append(f"MTU = {host['mtu']}")

    for client in db.clients.find({"connections.host_id": str(host["_id"])}):
        client_key = (
            db.keys.find_one({"_id": ObjectId(client["active_key_id"])})
            if client.get("active_key_id")
            else None
        )
        for conn in client.get("connections", []):
            if conn["host_id"] != str(host["_id"]):
                continue
            if conn.get("network_id") != network_id:
                continue
            client_membership = next(
                (m for m in client.get("network_memberships", []) if m["network_id"] == conn["network_id"]),
                None,
            )
            if not client_membership:
                continue
            lines.append("")
            lines.append(f"[Peer]  # Client: {client.get('name', '')}")
            lines.append(f"PublicKey = {client_key.get('public_key', '') if client_key else ''}")
            lines.append(f"AllowedIPs = {client_membership['ip']}/32")
            if conn.get("persistent_keepalive"):
                lines.append(f"PersistentKeepalive = {conn['persistent_keepalive']}")

    for pconn in host.get("peer_connections", []):
        if pconn.get("network_id") != network_id:
            continue
        peer_host = db.hosts.find_one({"_id": ObjectId(pconn["peer_host_id"])})
        if not peer_host:
            continue
        peer_key = (
            db.keys.find_one({"_id": ObjectId(peer_host["active_key_id"])})
            if peer_host.get("active_key_id")
            else None
        )
        lines.append("")
        lines.append(f"[Peer]  # Host: {peer_host.get('name', '')}")
        lines.append(f"PublicKey = {peer_key.get('public_key', '') if peer_key else ''}")
        endpoint = pconn.get("endpoint_override") or _computed_endpoint(peer_host)
        if endpoint:
            lines.append(f"Endpoint = {endpoint}")
        aset = db.allowed_ips.find_one({"_id": ObjectId(pconn["allowed_ips_set_id"])})
        lines.append(f"AllowedIPs = {aset['cidrs'] if aset else ''}")
        if pconn.get("persistent_keepalive"):
            lines.append(f"PersistentKeepalive = {pconn['persistent_keepalive']}")

    return "\n".join(lines) + "\n"


def render_client_interface_config(client_id, network_id, allowed_ips_override=None):
    db = get_db()
    client = db.clients.find_one({"_id": ObjectId(client_id)})
    if not client:
        raise ValueError("Client not found")

    membership = _find_membership(client.get("network_memberships", []), network_id)
    if not membership:
        raise ValueError("Client is not a member of that network")

    key = db.keys.find_one({"_id": ObjectId(client["active_key_id"])}) if client.get("active_key_id") else None
    private_key = decrypt_private_key(key["private_key"]) if key and key.get("private_key") else ""

    lines = ["[Interface]", f"PrivateKey = {private_key}"]
    address = _single_address(membership, db)
    if address:
        lines.append(f"Address = {address}")
    if client.get("dns_server_id"):
        dns_server = db.dns_servers.find_one({"_id": ObjectId(client["dns_server_id"])})
        if dns_server:
            lines.append(f"DNS = {dns_server['ips']}")

    for conn in client.get("connections", []):
        if conn.get("network_id") != network_id:
            continue
        host = db.hosts.find_one({"_id": ObjectId(conn["host_id"])})
        if not host:
            continue
        host_key = (
            db.keys.find_one({"_id": ObjectId(host["active_key_id"])}) if host.get("active_key_id") else None
        )
        lines.append("")
        lines.append(f"[Peer]  # Host: {host.get('name', '')}")
        lines.append(f"PublicKey = {host_key.get('public_key', '') if host_key else ''}")
        host_endpoint = _computed_endpoint(host)
        if host_endpoint:
            lines.append(f"Endpoint = {host_endpoint}")
        aset = db.allowed_ips.find_one({"_id": ObjectId(conn["allowed_ips_set_id"])})
        allowed = aset["cidrs"] if aset else ""
        if allowed_ips_override:
            allowed = allowed_ips_override
        lines.append(f"AllowedIPs = {allowed}")
        if conn.get("persistent_keepalive"):
            lines.append(f"PersistentKeepalive = {conn['persistent_keepalive']}")

    return "\n".join(lines) + "\n"
