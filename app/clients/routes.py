from bson import ObjectId
from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.clients import bp
from app.clients.forms import ClientConnectionForm, ClientCreateForm, ClientForm
from app.extensions import get_db
from app.hosts.forms import NetworkMembershipForm
from app.keys.service import deactivate_key, generate_and_store_key, store_provided_key
from app.services.tunnel import render_client_config
from app.utils.crypto import is_valid_wg_key, keypair_matches
from app.utils.ipam import ip_in_network


def _find_client_or_404(client_id):
    return get_db().clients.find_one({"_id": ObjectId(client_id)})


def _membership_ip(memberships, network_id):
    return next((m["ip"] for m in memberships if m["network_id"] == network_id), None)


@bp.route("/")
@login_required
def list_clients():
    clients = list(get_db().clients.find().sort("name", 1))
    return render_template("clients/list.html", clients=clients)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_client():
    form = ClientCreateForm()
    if form.validate_on_submit():
        if form.key_source.data == "provide":
            public_key = (form.public_key.data or "").strip()
            private_key = (form.private_key.data or "").strip()
            if not is_valid_wg_key(public_key) or not is_valid_wg_key(private_key):
                flash("Public/private key must be valid base64-encoded 32-byte WireGuard keys.", "danger")
                return render_template("clients/form.html", form=form, title="New Client")
            if not keypair_matches(public_key, private_key):
                flash("Provided public key does not match the private key.", "danger")
                return render_template("clients/form.html", form=form, title="New Client")

        client_doc = {
            "name": form.name.data,
            "dns": form.dns.data or "",
            "network_memberships": [],
            "connections": [],
            "active_key_id": None,
        }
        client_id = get_db().clients.insert_one(client_doc).inserted_id

        if form.key_source.data == "provide":
            key_id = store_provided_key("client", client_id, public_key, private_key)
        else:
            key_id = generate_and_store_key("client", client_id)
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

    connections = []
    for idx, conn in enumerate(client.get("connections", [])):
        host = db.hosts.find_one({"_id": ObjectId(conn["host_id"])})
        connections.append({"index": idx, "connection": conn, "host": host})

    return render_template(
        "clients/detail.html",
        client=client,
        networks=networks,
        key=key,
        connections=connections,
    )


@bp.route("/<client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    client = _find_client_or_404(client_id)
    if not client:
        flash("Client not found.", "danger")
        return redirect(url_for("clients.list_clients"))

    form = ClientForm(data=client)
    if form.validate_on_submit():
        get_db().clients.update_one(
            {"_id": client["_id"]},
            {"$set": {"name": form.name.data, "dns": form.dns.data or ""}},
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

    db.keys.delete_many({"owner_type": "client", "owner_id": client_id})
    db.clients.delete_one({"_id": client["_id"]})
    flash("Client deleted.", "success")
    return redirect(url_for("clients.list_clients"))


@bp.route("/<client_id>/rotate-key", methods=["POST"])
@login_required
def rotate_key(client_id):
    client = _find_client_or_404(client_id)
    if not client:
        return redirect(url_for("clients.list_clients"))
    if client.get("active_key_id"):
        deactivate_key(client["active_key_id"])
    new_key_id = generate_and_store_key("client", client_id)
    get_db().clients.update_one({"_id": client["_id"]}, {"$set": {"active_key_id": new_key_id}})
    flash("Key rotated. Existing tunnel files using the old key will stop working.", "success")
    return redirect(url_for("clients.detail", client_id=client_id))


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
                    "allowed_ips": form.allowed_ips.data,
                    "persistent_keepalive": form.persistent_keepalive.data,
                }}},
            )
            flash("Connection added.", "success")
            return redirect(url_for("clients.detail", client_id=client_id))

    network_cidrs = {nid: net["cidr"] for nid, net in networks.items()}
    return render_template(
        "clients/connection_form.html", form=form, client=client, network_cidrs=network_cidrs
    )


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


@bp.route("/<client_id>/config/<int:index>")
@login_required
def config(client_id, index):
    client = _find_client_or_404(client_id)
    if not client:
        flash("Client not found.", "danger")
        return redirect(url_for("clients.list_clients"))

    override = request.args.get("allowed_ips_override") or None
    text = render_client_config(client_id, connection_index=index, allowed_ips_override=override)
    if request.args.get("download"):
        filename = f"{client['name']}.conf"
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return Response(text, mimetype="text/plain")
