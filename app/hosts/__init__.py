# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask import Blueprint

bp = Blueprint("hosts", __name__, url_prefix="/hosts")

from app.hosts import routes  # noqa: E402,F401
