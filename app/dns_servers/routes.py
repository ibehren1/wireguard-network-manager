import ipaddress

from bson import ObjectId
from flask import flash, redirect, render_template, url_for
from flask_login import login_required

from app.dns_servers import bp
from app.dns_servers.forms import DnsServerForm
from app.extensions import get_db


def _valid_ip_list(raw):
    entries = [e.strip() for e in raw.split(",")]
    if not entries or any(not e for e in entries):
        return False
    for entry in entries:
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            return False
    return True


def _in_use(dns_server_id):
    db = get_db()
    sid = str(dns_server_id)
    return (
        db.hosts.count_documents({"network_memberships.dns_server_id": sid}) > 0
        or db.clients.count_documents({"dns_server_id": sid}) > 0
    )


@bp.route("/")
@login_required
def list_dns_servers():
    servers = list(get_db().dns_servers.find().sort("name", 1))
    return render_template("dns_servers/list.html", servers=servers)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_dns_server():
    form = DnsServerForm()
    if form.validate_on_submit():
        ips = form.ips.data.strip()
        if not _valid_ip_list(ips):
            flash("IPs must be a comma-separated list of valid IPv4 addresses.", "danger")
            return render_template("dns_servers/form.html", form=form, title="New DNS Server")

        get_db().dns_servers.insert_one({"name": form.name.data, "ips": ips})
        flash("DNS Server created.", "success")
        return redirect(url_for("dns_servers.list_dns_servers"))
    return render_template("dns_servers/form.html", form=form, title="New DNS Server")


@bp.route("/<dns_server_id>/edit", methods=["GET", "POST"])
@login_required
def edit_dns_server(dns_server_id):
    db = get_db()
    server = db.dns_servers.find_one({"_id": ObjectId(dns_server_id)})
    if not server:
        flash("DNS Server not found.", "danger")
        return redirect(url_for("dns_servers.list_dns_servers"))

    form = DnsServerForm(data={"name": server["name"], "ips": server["ips"]})
    if form.validate_on_submit():
        ips = form.ips.data.strip()
        if not _valid_ip_list(ips):
            flash("IPs must be a comma-separated list of valid IPv4 addresses.", "danger")
            return render_template("dns_servers/form.html", form=form, title="Edit DNS Server")

        db.dns_servers.update_one(
            {"_id": server["_id"]},
            {"$set": {"name": form.name.data, "ips": ips}},
        )
        flash("DNS Server updated.", "success")
        return redirect(url_for("dns_servers.list_dns_servers"))
    return render_template("dns_servers/form.html", form=form, title="Edit DNS Server")


@bp.route("/<dns_server_id>/delete", methods=["POST"])
@login_required
def delete_dns_server(dns_server_id):
    if _in_use(dns_server_id):
        flash("Cannot delete a DNS Server that is in use by a Host or Client.", "danger")
        return redirect(url_for("dns_servers.list_dns_servers"))
    get_db().dns_servers.delete_one({"_id": ObjectId(dns_server_id)})
    flash("DNS Server deleted.", "success")
    return redirect(url_for("dns_servers.list_dns_servers"))
