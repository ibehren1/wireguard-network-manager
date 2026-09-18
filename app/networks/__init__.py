# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask import Blueprint

bp = Blueprint("networks", __name__, url_prefix="/networks")

from app.networks import routes  # noqa: E402,F401
