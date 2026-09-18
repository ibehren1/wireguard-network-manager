from bson import ObjectId
from flask import Response, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.clients import bp
from app.clients.forms import AssignExistingKeyForm, ClientConnectionForm, ClientCreateForm, ClientForm
from app.extensions import get_db
from app.hosts.forms import NetworkMembershipForm
from app.keys.service import assign_key_to_owner, generate_and_store_key, key_usages, store_provided_key
from app.services.graph import build_client_graph
from app.services.tunnel import render_client_interface_config
from app.utils.crypto import is_valid_wg_key
from app.utils.ipam import ip_in_network


def _find_client_or_404(client_id):
    return get_db().clients.find_one({"_id": ObjectId(client_id)})


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
def list_clients():
    clients = list(get_db().clients.find().sort("name", 1))
    return render_template("clients/list.html", clients=clients)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_client():
    form = ClientCreateForm()
    form.existing_key_id.choices = _key_choices(get_db())
    form.dns_server_id.choices = _dns_server_choices(get_db())
    if form.validate_on_submit():
        private_key = (form.private_key.data or "").strip()
        if form.key_source.data == "new" and private_key and not is_valid_wg_key(private_key):
            flash("Private key must be a valid base64-encoded 32-byte WireGuard key.", "danger")
            return render_template("clients/form.html", form=form, title="New Client")
        if form.key_source.data == "existing" and not form.existing_key_id.data:
            flash("Choose an existing key.", "danger")
            return render_template("clients/form.html", form=form, title="New Client")

        client_doc = {
            "name": form.name.data,
            "dns_server_id": form.dns_server_id.data or None,
            "network_memberships": [],
            "connections": [],
            "active_key_id": None,
        }
        client_id = get_db().clients.insert_one(client_doc).inserted_id

        if form.key_source.data == "existing":
            prior_usages = key_usages(form.existing_key_id.data)
            assign_key_to_owner(form.existing_key_id.data, "client", client_id)
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
            get_db().clients.update_one({"_id": client_id}, {"$set": {"active_key_id": key_id}})

        flash("Client created.", "success")
        return redirect(url_for("clients.detail", client_id=str(client_id)))
    return render_template("clients/form.html", form=form, title="New Client")


@bp.route("/<client_id>")
@login_required
def detail(client_id):
    db = get_db()
    client = _find_client_or_404(client_id)
    if not client:
        flash("Client not found.", "danger")
        return redirect(url_for("clients.list_clients"))

    networks = {str(n["_id"]): n for n in db.networks.find()}
    key = db.keys.find_one({"_id": ObjectId(client["active_key_id"])}) if client.get("active_key_id") else None
    dns_server = (
        db.dns_servers.find_one({"_id": ObjectId(client["dns_server_id"])})
        if client.get("dns_server_id")
        else None
    )
    allowed_ips_sets = {str(a["_id"]): a for a in db.allowed_ips.find()}

    interfaces = []
    for m in client.get("network_memberships", []):
        conns = []
        for idx, conn in enumerate(client.get("connections", [])):
            if conn.get("network_id") != m["network_id"]:
                continue
            host = db.hosts.find_one({"_id": ObjectId(conn["host_id"])})
            conns.append({"index": idx, "connection": conn, "host": host})
        interfaces.append({
            "membership": m,
            "network": networks.get(m["network_id"]),
            "interface_name": m.get("interface_name") or "wg?",
            "connections": conns,
        })

    return render_template(
        "clients/detail.html",
        client=client,
        networks=networks,
        key=key,
        dns_server=dns_server,
        allowed_ips_sets=allowed_ips_sets,
        interfaces=interfaces,
    )


@bp.route("/<client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    client = _find_client_or_404(client_id)
    if not client:
        flash("Client not found.", "danger")
        return redirect(url_for("clients.list_clients"))

    form = ClientForm(data={**client, "dns_server_id": client.get("dns_server_id") or ""})
    form.dns_server_id.choices = _dns_server_choices(get_db())
    if form.validate_on_submit():
        get_db().clients.update_one(
            {"_id": client["_id"]},
            {"$set": {"name": form.name.data, "dns_server_id": form.dns_server_id.data or None}},
        )
        flash("Client updated.", "success")
        return redirect(url_for("clients.detail", client_id=client_id))
    return render_template("clients/form.html", form=form, title="Edit Client")


@bp.route("/<client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id):
    db = get_db()
    client = _find_client_or_404(client_id)
    if not client:
        return redirect(url_for("clients.list_clients"))

    db.clients.delete_one({"_id": client["_id"]})
    flash("Client deleted.", "success")
    return redirect(url_for("clients.list_clients"))


@bp.route("/<client_id>/rotate-key", methods=["POST"])
@login_required
def rotate_key(client_id):
    client = _find_client_or_404(client_id)
    if not client:
        return redirect(url_for("clients.list_clients"))
    new_key_id = generate_and_store_key(f"{client.get('name') or client_id} key")
    get_db().clients.update_one({"_id": client["_id"]}, {"$set": {"active_key_id": new_key_id}})
    flash("Key rotated. Existing tunnel files using the old key will stop working. The old key is left in the system in case it's still used elsewhere.", "success")
    return redirect(url_for("clients.detail", client_id=client_id))


@bp.route("/<client_id>/assign-key", methods=["GET", "POST"])
@login_required
def assign_existing_key(client_id):
    db = get_db()
    client = _find_client_or_404(client_id)
    if not client:
        return redirect(url_for("clients.list_clients"))

    form = AssignExistingKeyForm()
    form.existing_key_id.choices = _key_choices(db)

    if form.validate_on_submit():
        prior_usages = key_usages(form.existing_key_id.data)
        assign_key_to_owner(form.existing_key_id.data, "client", client_id)
        flash("Key assigned. Existing tunnel files using the old key will stop working.", "success")
        if prior_usages:
            used_by = ", ".join(u["name"] for u in prior_usages)
            flash(
                f"This key is already used by: {used_by} — it will now also be used by {client.get('name', client_id)}. "
                "This isn't recommended (WireGuard keys are meant to be unique per peer).",
                "warning",
            )
        return redirect(url_for("clients.detail", client_id=client_id))

    return render_template("clients/assign_key_form.html", form=form, client=client)


@bp.route("/<client_id>/networks/add", methods=["GET", "POST"])
@login_required
def add_network_membership(client_id):
    db = get_db()
    client = _find_client_or_404(client_id)
    if not client:
        return redirect(url_for("clients.list_clients"))

    form = NetworkMembershipForm()
    networks = list(db.networks.find())
    form.network_id.choices = [(str(n["_id"]), f"{n['name']} ({n['cidr']})") for n in networks]

    if form.validate_on_submit():
        network = db.networks.find_one({"_id": ObjectId(form.network_id.data)})
        if not network:
            flash("Network not found.", "danger")
        elif any(m["network_id"] == form.network_id.data for m in client.get("network_memberships", [])):
            flash("Client is already a member of that network.", "danger")
        elif not ip_in_network(form.ip.data, network["cidr"]):
            flash("IP is not within the selected network's CIDR.", "danger")
        elif db.hosts.count_documents({"network_memberships": {"$elemMatch": {"network_id": form.network_id.data, "ip": form.ip.data}}}) > 0 \
                or db.clients.count_documents({"network_memberships": {"$elemMatch": {"network_id": form.network_id.data, "ip": form.ip.data}}}) > 0:
            flash("That IP is already assigned within the network.", "danger")
        else:
            db.clients.update_one(
                {"_id": client["_id"]},
                {"$push": {"network_memberships": {"network_id": form.network_id.data, "ip": form.ip.data}}},
            )
            flash("Network membership added.", "success")
            return redirect(url_for("clients.detail", client_id=client_id))

    return render_template("clients/network_form.html", form=form, client=client)


@bp.route("/<client_id>/networks/<network_id>/remove", methods=["POST"])
@login_required
def remove_network_membership(client_id, network_id):
    db = get_db()
    client = _find_client_or_404(client_id)
    if not client:
        return redirect(url_for("clients.list_clients"))

    if any(c["network_id"] == network_id for c in client.get("connections", [])):
        flash("Remove connections on this network before removing membership.", "danger")
        return redirect(url_for("clients.detail", client_id=client_id))

    db.clients.update_one({"_id": client["_id"]}, {"$pull": {"network_memberships": {"network_id": network_id}}})
    flash("Network membership removed.", "success")
    return redirect(url_for("clients.detail", client_id=client_id))


@bp.route("/<client_id>/connections/add", methods=["GET", "POST"])
@login_required
def add_connection(client_id):
    db = get_db()
    client = _find_client_or_404(client_id)
    if not client:
        return redirect(url_for("clients.list_clients"))

    form = ClientConnectionForm()
    hosts = list(db.hosts.find())
    form.host_id.choices = [(str(h["_id"]), h["name"]) for h in hosts]
    form.allowed_ips_set_id.choices = _allowed_ips_choices(db)
    networks = {str(n["_id"]): n for n in db.networks.find()}
    my_network_ids = {m["network_id"] for m in client.get("network_memberships", [])}
    form.network_id.choices = [
        (nid, f"{net['name']} ({net['cidr']})") for nid, net in networks.items() if nid in my_network_ids
    ]

    if form.validate_on_submit():
        host = db.hosts.find_one({"_id": ObjectId(form.host_id.data)})
        if not host:
            flash("Host not found.", "danger")
        elif not _membership_ip(host.get("network_memberships", []), form.network_id.data):
            flash("Host is not a member of the selected network.", "danger")
        elif not _membership_ip(client.get("network_memberships", []), form.network_id.data):
            flash("Client is not a member of the selected network.", "danger")
        elif any(
            c["host_id"] == form.host_id.data and c["network_id"] == form.network_id.data
            for c in client.get("connections", [])
        ):
            flash("A connection to that host on that network already exists.", "danger")
        else:
            db.clients.update_one(
                {"_id": client["_id"]},
                {"$push": {"connections": {
                    "host_id": form.host_id.data,
                    "network_id": form.network_id.data,
                    "allowed_ips_set_id": form.allowed_ips_set_id.data,
                    "persistent_keepalive": form.persistent_keepalive.data,
                }}},
            )
            flash("Connection added.", "success")
            return redirect(url_for("clients.detail", client_id=client_id))

    return render_template("clients/connection_form.html", form=form, client=client)


@bp.route("/<client_id>/connections/<int:index>/remove", methods=["POST"])
@login_required
def remove_connection(client_id, index):
    db = get_db()
    client = _find_client_or_404(client_id)
    if not client:
        return redirect(url_for("clients.list_clients"))

    connections = client.get("connections", [])
    if 0 <= index < len(connections):
        del connections[index]
        db.clients.update_one({"_id": client["_id"]}, {"$set": {"connections": connections}})
        flash("Connection removed.", "success")
    return redirect(url_for("clients.detail", client_id=client_id))


@bp.route("/<client_id>/config/<network_id>")
@login_required
def config(client_id, network_id):
    client = _find_client_or_404(client_id)
    if not client:
        flash("Client not found.", "danger")
        return redirect(url_for("clients.list_clients"))

    override = request.args.get("allowed_ips_override") or None
    try:
        text = render_client_interface_config(client_id, network_id, allowed_ips_override=override)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("clients.detail", client_id=client_id))

    if request.args.get("download"):
        interface_name = next(
            (m.get("interface_name") for m in client.get("network_memberships", []) if m["network_id"] == network_id),
            None,
        ) or network_id
        filename = f"{client['name']}-{interface_name}.conf"
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return Response(text, mimetype="text/plain")


@bp.route("/<client_id>/graph.json")
@login_required
def graph_json(client_id):
    return jsonify(build_client_graph(client_id))
