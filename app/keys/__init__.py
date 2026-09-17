from flask import Blueprint

bp = Blueprint("keys", __name__, url_prefix="/keys")

from app.keys import routes  # noqa: E402,F401
