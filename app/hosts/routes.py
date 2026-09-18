from bson import ObjectId
from flask import Response, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import get_db
from app.hosts import bp
from app.hosts.forms import (
    AssignExistingKeyForm,
    HostForm,
    HostPeerConnectionForm,
    INTERFACE_TYPE_LABELS,
    InterfaceForm,
)
from app.keys.service import assign_key_to_host_interface, generate_and_store_key, key_usages
from app.services.graph import build_host_graph
from app.services.tunnel import render_host_interface_config
from app.utils.ipam import ip_in_network

# Which Network `network_type` an interface_type is allowed to attach to.
INTERFACE_TYPE_NETWORK_TYPE = {
    "p2p": "p2p",
    "client": "host_network",
    "client_non_wg": "host_network",
}

NETWORK_TYPE_LABELS = {"p2p": "P2P", "ipam": "IPAM CIDR Space", "host_network": "Host Network"}


def _find_host_or_404(host_id):
    return get_db().hosts.find_one({"_id": ObjectId(host_id)})


def _membership_ip(memberships, network_id):
    return next((m["ip"] for m in memberships if m["network_id"] == network_id), None)


def _find_membership(memberships, network_id):
    return next((m for m in memberships if m["network_id"] == network_id), None)


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


def _interface_networks(db):
    """Non-ipam networks — the only valid interface-attachment targets."""
    return list(db.networks.find({"network_type": {"$ne": "ipam"}}).sort("name", 1))


def _interface_network_choices(db, networks=None):
    networks = networks if networks is not None else _interface_networks(db)
    return [(str(n["_id"]), f"{n['name']} ({n['cidr']})") for n in networks]


def _interface_network_types_json(networks):
    return {str(n["_id"]): n.get("network_type", "ipam") for n in networks}


@bp.route("/")
@login_required
def list_hosts():
    hosts = list(get_db().hosts.find().sort("name", 1))
    return render_template("hosts/list.html", hosts=hosts)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_host():
    form = HostForm()
    if form.validate_on_submit():
        host_doc = {
            "name": form.name.data,
            "network_memberships": [],
            "peer_connections": [],
        }
        host_id = get_db().hosts.insert_one(host_doc).inserted_id
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
    dns_servers = {str(d["_id"]): d for d in db.dns_servers.find()}
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
            peer_membership = _find_membership(peer_host.get("network_memberships", []), pconn["network_id"])
            if pconn.get("endpoint_override"):
                computed_endpoint = pconn["endpoint_override"]
            elif peer_membership and peer_membership.get("hostname") and peer_membership.get("listen_port"):
                computed_endpoint = f"{peer_membership['hostname']}:{peer_membership['listen_port']}"
            else:
                computed_endpoint = "-"
            peer_hosts.append({"host": peer_host, "connection": pconn, "computed_endpoint": computed_endpoint})

    other_hosts = list(db.hosts.find({"_id": {"$ne": host["_id"]}}))

    interfaces = []
    for m in host.get("network_memberships", []):
        interface_type = m.get("interface_type", "client")
        dns_id = m.get("dns_server_id")
        key_id = m.get("active_key_id")
        key = db.keys.find_one({"_id": ObjectId(key_id)}) if key_id else None
        interfaces.append({
            "membership": m,
            "network": networks.get(m["network_id"]),
            "interface_name": m.get("interface_name") or "wg?",
            "interface_type": interface_type,
            "interface_type_label": INTERFACE_TYPE_LABELS.get(interface_type, interface_type),
            "hostname": m.get("hostname"),
            "listen_port": m.get("listen_port"),
            "dns_server": dns_servers.get(str(dns_id)) if dns_id else None,
            "mtu": m.get("mtu"),
            "key": key,
        })

    return render_template(
        "hosts/detail.html",
        host=host,
        networks=networks,
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

    form = HostForm(data={"name": host.get("name", "")})
    if form.validate_on_submit():
        get_db().hosts.update_one(
            {"_id": host["_id"]},
            {"$set": {"name": form.name.data}},
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


@bp.route("/<host_id>/interfaces/<network_id>/rotate-key", methods=["POST"])
@login_required
def rotate_interface_key(host_id, network_id):
    db = get_db()
    host = _find_host_or_404(host_id)
    if not host:
        return redirect(url_for("hosts.list_hosts"))

    membership = _find_membership(host.get("network_memberships", []), network_id)
    if not membership:
        flash("Interface not found.", "danger")
        return redirect(url_for("hosts.detail", host_id=host_id))
    if membership.get("interface_type", "client") == "client_non_wg":
        flash("Non-WireGuard interfaces don't have a key.", "danger")
        return redirect(url_for("hosts.detail", host_id=host_id))

    interface_name = membership.get("interface_name") or "wg?"
    new_key_id = generate_and_store_key(f"{host.get('name') or host_id} {interface_name} key")
    assign_key_to_host_interface(new_key_id, host_id, network_id)
    flash("Key rotated. Existing tunnel files using the old key will stop working. The old key is left in the system in case it's still used elsewhere.", "success")
    return redirect(url_for("hosts.detail", host_id=host_id))


@bp.route("/<host_id>/interfaces/<network_id>/assign-key", methods=["GET", "POST"])
@login_required
def assign_interface_key(host_id, network_id):
    db = get_db()
    host = _find_host_or_404(host_id)
    if not host:
        return redirect(url_for("hosts.list_hosts"))

    membership = _find_membership(host.get("network_memberships", []), network_id)
    if not membership:
        flash("Interface not found.", "danger")
        return redirect(url_for("hosts.detail", host_id=host_id))
    if membership.get("interface_type", "client") == "client_non_wg":
        flash("Non-WireGuard interfaces don't have a key.", "danger")
        return redirect(url_for("hosts.detail", host_id=host_id))

    interface_name = membership.get("interface_name") or "wg?"
    form = AssignExistingKeyForm()
    form.existing_key_id.choices = _key_choices(db)

    if form.validate_on_submit():
        prior_usages = key_usages(form.existing_key_id.data)
        assign_key_to_host_interface(form.existing_key_id.data, host_id, network_id)
        flash("Key assigned. Existing tunnel files using the old key will stop working.", "success")
        if prior_usages:
            used_by = ", ".join(u["name"] for u in prior_usages)
            flash(
                f"This key is already used by: {used_by} — it will now also be used by "
                f"{host.get('name', host_id)} ({interface_name}). "
                "This isn't recommended (WireGuard keys are meant to be unique per peer).",
                "warning",
            )
        return redirect(url_for("hosts.detail", host_id=host_id))

    return render_template(
        "hosts/assign_key_form.html", form=form, host=host,
        network_id=network_id, interface_name=interface_name,
    )


def _prepare_interface_form(db, form):
    networks = _interface_networks(db)
    form.network_id.choices = _interface_network_choices(db, networks)
    form.dns_server_id.choices = _dns_server_choices(db)
    return _interface_network_types_json(networks)


def _validate_network_for_type(network, interface_type):
    """Returns an error message string, or None if the network is a valid
    attachment target for this interface_type."""
    expected = INTERFACE_TYPE_NETWORK_TYPE.get(interface_type)
    actual = network.get("network_type", "ipam")
    if actual != expected:
        return (
            f"A {INTERFACE_TYPE_LABELS.get(interface_type, interface_type)} interface must attach to a "
            f"{NETWORK_TYPE_LABELS.get(expected, expected)} network."
        )
    return None


@bp.route("/<host_id>/interfaces/add", methods=["GET", "POST"])
@login_required
def add_interface(host_id):
    db = get_db()
    host = _find_host_or_404(host_id)
    if not host:
        return redirect(url_for("hosts.list_hosts"))

    form = InterfaceForm()
    network_types = _prepare_interface_form(db, form)
    if not form.interface_name.data:
        form.interface_name.data = f"wg{len(host.get('network_memberships', []))}"

    if form.validate_on_submit():
        network = db.networks.find_one({"_id": ObjectId(form.network_id.data)})
        type_error = _validate_network_for_type(network, form.interface_type.data) if network else None
        if not network:
            flash("Network not found.", "danger")
        elif type_error:
            flash(type_error, "danger")
        elif any(m["network_id"] == form.network_id.data for m in host.get("network_memberships", [])):
            flash("Host is already a member of that network.", "danger")
        elif not ip_in_network(form.ip.data, network["cidr"]):
            flash("IP is not within the selected network's CIDR.", "danger")
        elif db.hosts.count_documents({"network_memberships": {"$elemMatch": {"network_id": form.network_id.data, "ip": form.ip.data}}}) > 0 \
                or db.clients.count_documents({"network_memberships": {"$elemMatch": {"network_id": form.network_id.data, "ip": form.ip.data}}}) > 0:
            flash("That IP is already assigned within the network.", "danger")
        else:
            non_wg = form.interface_type.data == "client_non_wg"
            membership = {
                "network_id": form.network_id.data,
                "ip": form.ip.data,
                "interface_name": form.interface_name.data,
                "interface_type": form.interface_type.data,
                "hostname": None if non_wg else (form.hostname.data or None),
                "listen_port": None if non_wg else form.listen_port.data,
                "dns_server_id": None if non_wg else (form.dns_server_id.data or None),
                "mtu": None if non_wg else form.mtu.data,
                "active_key_id": None,
            }
            db.hosts.update_one(
                {"_id": host["_id"]},
                {"$push": {"network_memberships": membership}},
            )
            flash("Interface added.", "success")
            return redirect(url_for("hosts.detail", host_id=host_id))

    return render_template(
        "hosts/interface_form.html",
        form=form,
        host=host,
        title="Add Interface",
        mode="add",
        network_types=network_types,
    )


@bp.route("/<host_id>/interfaces/<network_id>/edit", methods=["GET", "POST"])
@login_required
def edit_interface(host_id, network_id):
    db = get_db()
    host = _find_host_or_404(host_id)
    if not host:
        return redirect(url_for("hosts.list_hosts"))

    membership = _find_membership(host.get("network_memberships", []), network_id)
    if not membership:
        flash("Interface not found.", "danger")
        return redirect(url_for("hosts.detail", host_id=host_id))

    form = InterfaceForm(data={
        "network_id": membership["network_id"],
        "ip": membership["ip"],
        "interface_name": membership.get("interface_name") or "",
        "interface_type": membership.get("interface_type", "client"),
        "hostname": membership.get("hostname") or "",
        "listen_port": membership.get("listen_port"),
        "dns_server_id": membership.get("dns_server_id") or "",
        "mtu": membership.get("mtu"),
    })
    network_types = _prepare_interface_form(db, form)

    if form.validate_on_submit():
        network = db.networks.find_one({"_id": ObjectId(form.network_id.data)})
        type_error = _validate_network_for_type(network, form.interface_type.data) if network else None
        changing_network = form.network_id.data != network_id
        if not network:
            flash("Network not found.", "danger")
        elif type_error:
            flash(type_error, "danger")
        elif changing_network and any(m["network_id"] == form.network_id.data for m in host.get("network_memberships", [])):
            flash("Host is already a member of that network.", "danger")
        elif changing_network and (
            any(p["network_id"] == network_id for p in host.get("peer_connections", []))
            or db.clients.count_documents({"connections": {"$elemMatch": {"host_id": host_id, "network_id": network_id}}}) > 0
        ):
            flash("Remove Host-Host peer connections / Client connections on this network before changing its network.", "danger")
        elif not ip_in_network(form.ip.data, network["cidr"]):
            flash("IP is not within the selected network's CIDR.", "danger")
        elif (
            db.hosts.count_documents({
                "_id": {"$ne": host["_id"]},
                "network_memberships": {"$elemMatch": {"network_id": form.network_id.data, "ip": form.ip.data}},
            }) > 0
            or db.clients.count_documents({
                "network_memberships": {"$elemMatch": {"network_id": form.network_id.data, "ip": form.ip.data}},
            }) > 0
            or (
                not changing_network
                and form.ip.data != membership["ip"]
                and any(
                    m["network_id"] == form.network_id.data and m["ip"] == form.ip.data
                    for m in host.get("network_memberships", [])
                )
            )
        ):
            flash("That IP is already assigned within the network.", "danger")
        else:
            non_wg = form.interface_type.data == "client_non_wg"
            db.hosts.update_one(
                {"_id": host["_id"]},
                {"$set": {
                    "network_memberships.$[elem].network_id": form.network_id.data,
                    "network_memberships.$[elem].ip": form.ip.data,
                    "network_memberships.$[elem].interface_name": form.interface_name.data,
                    "network_memberships.$[elem].interface_type": form.interface_type.data,
                    "network_memberships.$[elem].hostname": None if non_wg else (form.hostname.data or None),
                    "network_memberships.$[elem].listen_port": None if non_wg else form.listen_port.data,
                    "network_memberships.$[elem].dns_server_id": None if non_wg else (form.dns_server_id.data or None),
                    "network_memberships.$[elem].mtu": None if non_wg else form.mtu.data,
                }},
                array_filters=[{"elem.network_id": network_id}],
            )
            flash("Interface updated.", "success")
            return redirect(url_for("hosts.detail", host_id=host_id))

    return render_template(
        "hosts/interface_form.html",
        form=form,
        host=host,
        title="Edit Interface",
        mode="edit",
        network_id=network_id,
        network_types=network_types,
    )


@bp.route("/<host_id>/interfaces/<network_id>/remove", methods=["POST"])
@login_required
def remove_interface(host_id, network_id):
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
    flash("Interface removed.", "success")
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
    # Host-Host peering only happens over p2p-type interfaces.
    my_network_ids = {
        m["network_id"] for m in host.get("network_memberships", [])
        if m.get("interface_type", "client") == "p2p"
    }
    form.network_id.choices = [
        (nid, f"{net['name']} ({net['cidr']})") for nid, net in networks.items() if nid in my_network_ids
    ]

    reference = []
    for other in other_hosts:
        for m in other.get("network_memberships", []):
            if m.get("interface_type", "client") != "p2p":
                continue
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
