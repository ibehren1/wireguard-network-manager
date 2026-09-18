from flask import jsonify, render_template
from flask_login import login_required

from app.services.graph import build_full_graph
from app.topology import bp


@bp.route("/")
@login_required
def index():
    return render_template("topology/index.html")


@bp.route("/graph.json")
@login_required
def graph_json():
    return jsonify(build_full_graph())
