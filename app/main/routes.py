# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask_login import login_required
from flask import render_template

from app.main import bp
from app.extensions import get_db


@bp.route("/")
@login_required
def dashboard():
    db = get_db()
    counts = {
        "networks": db.networks.count_documents({}),
        "hosts": db.hosts.count_documents({}),
        "clients": db.clients.count_documents({}),
        "keys": db.keys.count_documents({}),
    }
    return render_template("dashboard.html", counts=counts)
