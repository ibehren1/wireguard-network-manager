import ipaddress

from bson import ObjectId
from flask import flash, redirect, render_template, url_for
from flask_login import login_required

from app.allowed_ips import bp
from app.allowed_ips.forms import AllowedIpsSetForm
from app.extensions import get_db


def _valid_cidr_list(raw):
    entries = [e.strip() for e in raw.split(",")]
    if not entries or any(not e for e in entries):
        return False
    for entry in entries:
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError:
            return False
    return True


def _in_use(allowed_ips_set_id):
    db = get_db()
    sid = str(allowed_ips_set_id)
    return (
        db.clients.count_documents({"connections.allowed_ips_set_id": sid}) > 0
        or db.hosts.count_documents({"peer_connections.allowed_ips_set_id": sid}) > 0
    )


@bp.route("/")
@login_required
def list_allowed_ips_sets():
    sets = list(get_db().allowed_ips.find().sort("name", 1))
    return render_template("allowed_ips/list.html", sets=sets)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_allowed_ips_set():
    form = AllowedIpsSetForm()
    if form.validate_on_submit():
        cidrs = form.cidrs.data.strip()
        if not _valid_cidr_list(cidrs):
            flash("CIDRs must be a comma-separated list of valid IPv4 networks.", "danger")
            return render_template("allowed_ips/form.html", form=form, title="New Allowed IPs Set")

        get_db().allowed_ips.insert_one({"name": form.name.data, "cidrs": cidrs})
        flash("Allowed IPs set created.", "success")
        return redirect(url_for("allowed_ips.list_allowed_ips_sets"))
    return render_template("allowed_ips/form.html", form=form, title="New Allowed IPs Set")


@bp.route("/<allowed_ips_set_id>/edit", methods=["GET", "POST"])
@login_required
def edit_allowed_ips_set(allowed_ips_set_id):
    db = get_db()
    aset = db.allowed_ips.find_one({"_id": ObjectId(allowed_ips_set_id)})
    if not aset:
        flash("Allowed IPs set not found.", "danger")
        return redirect(url_for("allowed_ips.list_allowed_ips_sets"))

    form = AllowedIpsSetForm(data={"name": aset["name"], "cidrs": aset["cidrs"]})
    if form.validate_on_submit():
        cidrs = form.cidrs.data.strip()
        if not _valid_cidr_list(cidrs):
            flash("CIDRs must be a comma-separated list of valid IPv4 networks.", "danger")
            return render_template("allowed_ips/form.html", form=form, title="Edit Allowed IPs Set")

        db.allowed_ips.update_one(
            {"_id": aset["_id"]},
            {"$set": {"name": form.name.data, "cidrs": cidrs}},
        )
        flash("Allowed IPs set updated.", "success")
        return redirect(url_for("allowed_ips.list_allowed_ips_sets"))
    return render_template("allowed_ips/form.html", form=form, title="Edit Allowed IPs Set")


@bp.route("/<allowed_ips_set_id>/delete", methods=["POST"])
@login_required
def delete_allowed_ips_set(allowed_ips_set_id):
    if _in_use(allowed_ips_set_id):
        flash("Cannot delete an Allowed IPs set that is in use by a Client connection or Host peer connection.", "danger")
        return redirect(url_for("allowed_ips.list_allowed_ips_sets"))
    get_db().allowed_ips.delete_one({"_id": ObjectId(allowed_ips_set_id)})
    flash("Allowed IPs set deleted.", "success")
    return redirect(url_for("allowed_ips.list_allowed_ips_sets"))
