from flask import Blueprint

bp = Blueprint("networks", __name__, url_prefix="/networks")

from app.networks import routes  # noqa: E402,F401
