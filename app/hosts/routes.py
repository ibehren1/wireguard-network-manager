from bson import ObjectId
from flask import Response, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import get_db
from app.hosts import bp
from app.hosts.forms import (
    AssignExistingKeyForm,
    HostCreateForm,
    HostForm,
    HostPeerConnectionForm,
    NetworkMembershipForm,
)
from app.keys.service import assign_key_to_owner, generate_and_store_key, key_usages, store_provided_key
from app.services.graph import build_host_graph
from app.services.tunnel import render_host_interface_config
from app.utils.crypto import decrypt_private_key, is_valid_wg_key
from app.utils.ipam import ip_in_network


def _find_host_or_404(host_id):
    return get_db().hosts.find_one({"_id": ObjectId(host_id)})


def _membership_ip(memberships, network_id):
    return next((m["ip"] for m in memberships if m["network_id"] == network_id), None)


def _key_choices(db):
    return [
        (str(k["_id"]), k.get("name") or "(unnamed key)")
        for k in db.keys.find().sort("name", 1)
    ]


def _dns_server_choices(db):
    return [("", "None")] + [
        (str(d["_id"]), d["name"]) for d in db.dns_servers.find().sort("name", 1)
    ]


def _allowed_ips_choices(db):
    return [
        (str(a["_id"]), f"{a['name']} ({a['cidrs']})")
        for a in db.allowed_ips.find().sort("name", 1)
    ]


@bp.route("/")
@login_required
def list_hosts():
    hosts = list(get_db().hosts.find().sort("name", 1))
    return render_template("hosts/list.html", hosts=hosts)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_host():
    form = HostCreateForm()
    form.existing_key_id.choices = _key_choices(get_db())
    form.dns_server_id.choices = _dns_server_choices(get_db())
    if form.validate_on_submit():
        private_key = (form.private_key.data or "").strip()
        if form.key_source.data == "new" and private_key and not is_valid_wg_key(private_key):
            flash("Private key must be a valid base64-encoded 32-byte WireGuard key.", "danger")
            return render_template("hosts/form.html", form=form, title="New Host")
        if form.key_source.data == "existing" and not form.existing_key_id.data:
            flash("Choose an existing key.", "danger")
            return render_template("hosts/form.html", form=form, title="New Host")

        host_doc = {
            "name": form.name.data,
            "hostname": form.hostname.data or "",
            "listen_port": form.listen_port.data,
            "dns_server_id": form.dns_server_id.data or None,
            "mtu": form.mtu.data,
            "network_memberships": [],
            "peer_connections": [],
            "active_key_id": None,
        }
        host_id = get_db().hosts.insert_one(host_doc).inserted_id

        if form.key_source.data == "existing":
            prior_usages = key_usages(form.existing_key_id.data)
            assign_key_to_owner(form.existing_key_id.data, "host", host_id)
            if prior_usages:
                used_by = ", ".join(u["name"] for u in prior_usages)
                flash(
                    f"This key is already used by: {used_by} — it will now also be used by {form.name.data}. "
                    "This isn't recommended (WireGuard keys are meant to be unique per peer).",
                    "warning",
                )
        else:
            key_name = f"{form.name.data} key"
            if private_key:
                key_id = store_provided_key(key_name, private_key)
            else:
                key_id = generate_and_store_key(key_name)
            get_db().hosts.update_one({"_id": host_id}, {"$set": {"active_key_id": key_id}})

        flash("Host created.", "success")
        return redirect(url_for("hosts.detail", host_id=str(host_id)))
    return render_template("hosts/form.html", form=form, title="New Host")


@bp.route("/<host_id>")
@login_required
def detail(host_id):
    db = get_db()
    host = _find_host_or_404(host_id)
    if not host:
        flash("Host not found.", "danger")
        return redirect(url_for("hosts.list_hosts"))

    networks = {str(n["_id"]): n for n in db.networks.find()}
    key = db.keys.find_one({"_id": ObjectId(host["active_key_id"])}) if host.get("active_key_id") else None
    private_key = decrypt_private_key(key["private_key"]) if key and key.get("private_key") else None
    dns_server = (
        db.dns_servers.find_one({"_id": ObjectId(host["dns_server_id"])})
        if host.get("dns_server_id")
        else None
    )
    allowed_ips_sets = {str(a["_id"]): a for a in db.allowed_ips.find()}

    attached_clients = []
    for client in db.clients.find({"connections.host_id": str(host["_id"])}):
        for conn in client.get("connections", []):
            if conn["host_id"] == str(host["_id"]):
                attached_clients.append({"client": client, "connection": conn})

    peer_hosts = []
    for pconn in host.get("peer_connections", []):
        peer_host = db.hosts.find_one({"_id": ObjectId(pconn["peer_host_id"])})
        if peer_host:
            if pconn.get("endpoint_override"):
                computed_endpoint = pconn["endpoint_override"]
            elif peer_host.get("hostname") and peer_host.get("listen_port"):
                computed_endpoint = f"{peer_host['hostname']}:{peer_host['listen_port']}"
            else:
                computed_endpoint = "-"
            peer_hosts.append({"host": peer_host, "connection": pconn, "computed_endpoint": computed_endpoint})

    other_hosts = list(db.hosts.find({"_id": {"$ne": host["_id"]}}))

    interfaces = []
    for m in host.get("network_memberships", []):
        interfaces.append({
            "membership": m,
            "network": networks.get(m["network_id"]),
            "interface_name": m.get("interface_name") or "wg?",
        })

    return render_template(
        "hosts/detail.html",
        host=host,
        networks=networks,
        key=key,
        private_key=private_key,
        dns_server=dns_server,
        allowed_ips_sets=allowed_ips_sets,
        attached_clients=attached_clients,
        peer_hosts=peer_hosts,
        other_hosts=other_hosts,
        interfaces=interfaces,
    )


@bp.route("/<host_id>/edit", methods=["GET", "POST"])
@login_required
def edit_host(host_id):
    host = _find_host_or_404(host_id)
    if not host:
        flash("Host not found.", "danger")
        return redirect(url_for("hosts.list_hosts"))

    form = HostForm(data={**host, "dns_server_id": host.get("dns_server_id") or ""})
    form.dns_server_id.choices = _dns_server_choices(get_db())
    if form.validate_on_submit():
        get_db().hosts.update_one(
            {"_id": host["_id"]},
            {"$set": {
                "name": form.name.data,
                "hostname": form.hostname.data or "",
                "listen_port": form.listen_port.data,
                "dns_server_id": form.dns_server_id.data or None,
                "mtu": form.mtu.data,
            }},
        )
        flash("Host updated.", "success")
        return redirect(url_for("hosts.detail", host_id=host_id))
    return render_template("hosts/form.html", form=form, title="Edit Host")


@bp.route("/<host_id>/delete", methods=["POST"])
@login_required
def delete_host(host_id):
    db = get_db()
    host = _find_host_or_404(host_id)
    if not host:
        return redirect(url_for("hosts.list_hosts"))

    if db.clients.count_documents({"connections.host_id": host_id}) > 0:
        flash("Cannot delete a Host that has Clients connected to it.", "danger")
        return redirect(url_for("hosts.detail", host_id=host_id))
    if db.hosts.count_documents({"peer_connections.peer_host_id": host_id}) > 0:
        flash("Cannot delete a Host that another Host peers with.", "danger")
        return redirect(url_for("hosts.detail", host_id=host_id))

    db.hosts.delete_one({"_id": host["_id"]})
    flash("Host deleted.", "success")
    return redirect(url_for("hosts.list_hosts"))


@bp.route("/<host_id>/rotate-key", methods=["POST"])
@login_required
def rotate_key(host_id):
    host = _find_host_or_404(host_id)
    if not host:
        return redirect(url_for("hosts.list_hosts"))
    new_key_id = generate_and_store_key(f"{host.get('name') or host_id} key")
    get_db().hosts.update_one({"_id": host["_id"]}, {"$set": {"active_key_id": new_key_id}})
    flash("Key rotated. Existing tunnel files using the old key will stop working. The old key is left in the system in case it's still used elsewhere.", "success")
    return redirect(url_for("hosts.detail", host_id=host_id))


@bp.route("/<host_id>/assign-key", methods=["GET", "POST"])
@login_required
def assign_existing_key(host_id):
    db = get_db()
    host = _find_host_or_404(host_id)
    if not host:
        return redirect(url_for("hosts.list_hosts"))

    form = AssignExistingKeyForm()
    form.existing_key_id.choices = _key_choices(db)

    if form.validate_on_submit():
        prior_usages = key_usages(form.existing_key_id.data)
        assign_key_to_owner(form.existing_key_id.data, "host", host_id)
        flash("Key assigned. Existing tunnel files using the old key will stop working.", "success")
        if prior_usages:
            used_by = ", ".join(u["name"] for u in prior_usages)
            flash(
                f"This key is already used by: {used_by} — it will now also be used by {host.get('name', host_id)}. "
                "This isn't recommended (WireGuard keys are meant to be unique per peer).",
                "warning",
            )
        return redirect(url_for("hosts.detail", host_id=host_id))

    return render_template("hosts/assign_key_form.html", form=form, host=host)


@bp.route("/<host_id>/networks/add", methods=["GET", "POST"])
@login_required
def add_network_membership(host_id):
    db = get_db()
    host = _find_host_or_404(host_id)
    if not host:
        return redirect(url_for("hosts.list_hosts"))

    form = NetworkMembershipForm()
    networks = list(db.networks.find())
    form.network_id.choices = [(str(n["_id"]), f"{n['name']} ({n['cidr']})") for n in networks]
    if not form.interface_name.data:
        form.interface_name.data = f"wg{len(host.get('network_memberships', []))}"

    if form.validate_on_submit():
        network = db.networks.find_one({"_id": ObjectId(form.network_id.data)})
        if not network:
            flash("Network not found.", "danger")
        elif any(m["network_id"] == form.network_id.data for m in host.get("network_memberships", [])):
            flash("Host is already a member of that network.", "danger")
        elif not ip_in_network(form.ip.data, network["cidr"]):
            flash("IP is not within the selected network's CIDR.", "danger")
        elif db.hosts.count_documents({"network_memberships": {"$elemMatch": {"network_id": form.network_id.data, "ip": form.ip.data}}}) > 0 \
                or db.clients.count_documents({"network_memberships": {"$elemMatch": {"network_id": form.network_id.data, "ip": form.ip.data}}}) > 0:
            flash("That IP is already assigned within the network.", "danger")
        else:
            db.hosts.update_one(
                {"_id": host["_id"]},
                {"$push": {"network_memberships": {
                    "network_id": form.network_id.data,
                    "ip": form.ip.data,
                    "interface_name": form.interface_name.data,
                }}},
            )
            flash("Network membership added.", "success")
            return redirect(url_for("hosts.detail", host_id=host_id))

    return render_template("hosts/network_form.html", form=form, host=host)


@bp.route("/<host_id>/networks/<network_id>/remove", methods=["POST"])
@login_required
def remove_network_membership(host_id, network_id):
    db = get_db()
    host = _find_host_or_404(host_id)
    if not host:
        return redirect(url_for("hosts.list_hosts"))

    if any(p["network_id"] == network_id for p in host.get("peer_connections", [])):
        flash("Remove Host-Host peer connections on this network before removing membership.", "danger")
        return redirect(url_for("hosts.detail", host_id=host_id))
    if db.clients.count_documents({"connections": {"$elemMatch": {"host_id": host_id, "network_id": network_id}}}) > 0:
        flash("Remove Client connections on this network before removing membership.", "danger")
        return redirect(url_for("hosts.detail", host_id=host_id))

    db.hosts.update_one({"_id": host["_id"]}, {"$pull": {"network_memberships": {"network_id": network_id}}})
    flash("Network membership removed.", "success")
    return redirect(url_for("hosts.detail", host_id=host_id))


@bp.route("/<host_id>/peers/add", methods=["GET", "POST"])
@login_required
def add_peer_connection(host_id):
    db = get_db()
    host = _find_host_or_404(host_id)
    if not host:
        return redirect(url_for("hosts.list_hosts"))

    form = HostPeerConnectionForm()
    other_hosts = list(db.hosts.find({"_id": {"$ne": host["_id"]}}))
    form.peer_host_id.choices = [(str(h["_id"]), h["name"]) for h in other_hosts]
    form.allowed_ips_set_id.choices = _allowed_ips_choices(db)
    networks = {str(n["_id"]): n for n in db.networks.find()}
    my_network_ids = {m["network_id"] for m in host.get("network_memberships", [])}
    form.network_id.choices = [
        (nid, f"{net['name']} ({net['cidr']})") for nid, net in networks.items() if nid in my_network_ids
    ]

    reference = []
    for other in other_hosts:
        for m in other.get("network_memberships", []):
            if m["network_id"] in my_network_ids:
                net = networks.get(m["network_id"])
                reference.append({"host": other["name"], "network": net["name"] if net else "?", "ip": m["ip"]})

    if form.validate_on_submit():
        peer_host = db.hosts.find_one({"_id": ObjectId(form.peer_host_id.data)})
        if not peer_host:
            flash("Peer host not found.", "danger")
        elif not _membership_ip(peer_host.get("network_memberships", []), form.network_id.data):
            flash("Peer host is not a member of the selected network.", "danger")
        elif not _membership_ip(host.get("network_memberships", []), form.network_id.data):
            flash("This host is not a member of the selected network.", "danger")
        elif any(
            p["peer_host_id"] == form.peer_host_id.data and p["network_id"] == form.network_id.data
            for p in host.get("peer_connections", [])
        ):
            flash("A peer connection to that host on that network already exists.", "danger")
        else:
            # Reciprocal peer connection defaults to the SAME AllowedIpsSet the user picked
            # for the primary side (see CLAUDE.md) rather than auto-creating a /32 preset;
            # the user can edit it afterward via the remove/re-add flow.
            db.hosts.update_one(
                {"_id": host["_id"]},
                {"$push": {"peer_connections": {
                    "peer_host_id": form.peer_host_id.data,
                    "network_id": form.network_id.data,
                    "allowed_ips_set_id": form.allowed_ips_set_id.data,
                    "endpoint_override": form.endpoint_override.data or "",
                    "persistent_keepalive": form.persistent_keepalive.data,
                }}},
            )
            db.hosts.update_one(
                {"_id": peer_host["_id"]},
                {"$push": {"peer_connections": {
                    "peer_host_id": str(host["_id"]),
                    "network_id": form.network_id.data,
                    "allowed_ips_set_id": form.allowed_ips_set_id.data,
                    "endpoint_override": "",
                    "persistent_keepalive": None,
                }}},
            )
            flash("Peer connection created on both hosts.", "success")
            return redirect(url_for("hosts.detail", host_id=host_id))

    return render_template("hosts/peer_form.html", form=form, host=host, reference=reference)


@bp.route("/<host_id>/peers/<peer_host_id>/<network_id>/remove", methods=["POST"])
@login_required
def remove_peer_connection(host_id, peer_host_id, network_id):
    db = get_db()
    db.hosts.update_one(
        {"_id": ObjectId(host_id)},
        {"$pull": {"peer_connections": {"peer_host_id": peer_host_id, "network_id": network_id}}},
    )
    db.hosts.update_one(
        {"_id": ObjectId(peer_host_id)},
        {"$pull": {"peer_connections": {"peer_host_id": host_id, "network_id": network_id}}},
    )
    flash("Peer connection removed on both hosts.", "success")
    return redirect(url_for("hosts.detail", host_id=host_id))


@bp.route("/<host_id>/config/<network_id>")
@login_required
def config(host_id, network_id):
    host = _find_host_or_404(host_id)
    if not host:
        flash("Host not found.", "danger")
        return redirect(url_for("hosts.list_hosts"))

    try:
        text = render_host_interface_config(host_id, network_id)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("hosts.detail", host_id=host_id))

    if request.args.get("download"):
        interface_name = next(
            (m.get("interface_name") for m in host.get("network_memberships", []) if m["network_id"] == network_id),
            None,
        ) or network_id
        filename = f"{host['name']}-{interface_name}.conf"
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return Response(text, mimetype="text/plain")


@bp.route("/<host_id>/graph.json")
@login_required
def graph_json(host_id):
    return jsonify(build_host_graph(host_id))
