import ipaddress

from bson import ObjectId
from flask import flash, jsonify, redirect, render_template, url_for
from flask_login import login_required

from app.extensions import get_db
from app.networks import bp
from app.networks.forms import NetworkForm
from app.utils.ipam import next_free_ip, parse_network


def _overlaps_existing(cidr, exclude_id=None):
    network = ipaddress.ip_network(cidr, strict=True)
    db = get_db()
    query = {}
    if exclude_id:
        query["_id"] = {"$ne": ObjectId(exclude_id)}
    for doc in db.networks.find(query):
        other = ipaddress.ip_network(doc["cidr"], strict=True)
        if network.overlaps(other):
            return doc["name"]
    return None


def _used_ips(network_id):
    db = get_db()
    nid = str(network_id)
    ips = []
    for doc in db.hosts.find({"network_memberships.network_id": nid}, {"network_memberships": 1}):
        ips.extend(m["ip"] for m in doc["network_memberships"] if m["network_id"] == nid)
    for doc in db.clients.find({"network_memberships.network_id": nid}, {"network_memberships": 1}):
        ips.extend(m["ip"] for m in doc["network_memberships"] if m["network_id"] == nid)
    return ips


def _used_ip_count(network_id):
    return len(_used_ips(network_id))


@bp.route("/")
@login_required
def list_networks():
    db = get_db()
    networks = list(db.networks.find().sort("name", 1))
    for net in networks:
        net["used_count"] = _used_ip_count(net["_id"])
    return render_template("networks/list.html", networks=networks)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_network():
    form = NetworkForm()
    if form.validate_on_submit():
        try:
            parse_network(form.cidr.data)
        except ValueError:
            flash("CIDR must be a valid network address, e.g. 10.0.0.0/24.", "danger")
            return render_template("networks/form.html", form=form, title="New Network")

        conflict = _overlaps_existing(form.cidr.data)
        if conflict:
            flash(f"CIDR overlaps with existing network '{conflict}'.", "danger")
            return render_template("networks/form.html", form=form, title="New Network")

        get_db().networks.insert_one(
            {
                "name": form.name.data,
                "cidr": form.cidr.data,
                "description": form.description.data or "",
            }
        )
        flash("Network created.", "success")
        return redirect(url_for("networks.list_networks"))
    return render_template("networks/form.html", form=form, title="New Network")


@bp.route("/<network_id>/edit", methods=["GET", "POST"])
@login_required
def edit_network(network_id):
    db = get_db()
    net = db.networks.find_one({"_id": ObjectId(network_id)})
    if not net:
        flash("Network not found.", "danger")
        return redirect(url_for("networks.list_networks"))

    form = NetworkForm(data={"name": net["name"], "cidr": net["cidr"], "description": net["description"]})
    if form.validate_on_submit():
        try:
            parse_network(form.cidr.data)
        except ValueError:
            flash("CIDR must be a valid network address, e.g. 10.0.0.0/24.", "danger")
            return render_template("networks/form.html", form=form, title="Edit Network")

        conflict = _overlaps_existing(form.cidr.data, exclude_id=network_id)
        if conflict:
            flash(f"CIDR overlaps with existing network '{conflict}'.", "danger")
            return render_template("networks/form.html", form=form, title="Edit Network")

        db.networks.update_one(
            {"_id": ObjectId(network_id)},
            {"$set": {
                "name": form.name.data,
                "cidr": form.cidr.data,
                "description": form.description.data or "",
            }},
        )
        flash("Network updated.", "success")
        return redirect(url_for("networks.list_networks"))
    return render_template("networks/form.html", form=form, title="Edit Network")


@bp.route("/<network_id>/delete", methods=["POST"])
@login_required
def delete_network(network_id):
    if _used_ip_count(network_id) > 0:
        flash("Cannot delete a network that has Hosts or Clients assigned to it.", "danger")
        return redirect(url_for("networks.list_networks"))
    get_db().networks.delete_one({"_id": ObjectId(network_id)})
    flash("Network deleted.", "success")
    return redirect(url_for("networks.list_networks"))


@bp.route("/<network_id>/next-free-ip")
@login_required
def next_free_ip_api(network_id):
    db = get_db()
    net = db.networks.find_one({"_id": ObjectId(network_id)})
    if not net:
        return jsonify({"error": "not found"}), 404
    ip = next_free_ip(net["cidr"], _used_ips(network_id))
    return jsonify({"ip": ip, "cidr": net["cidr"]})
