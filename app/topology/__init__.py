# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask import Blueprint

bp = Blueprint("topology", __name__, url_prefix="/topology")

from app.topology import routes  # noqa: E402,F401
