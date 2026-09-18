# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask import Blueprint

bp = Blueprint("main", __name__)

from app.main import routes  # noqa: E402,F401
