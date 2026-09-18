# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import DataRequired


class AllowedIpsSetForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    cidrs = StringField(
        "CIDRs (comma-separated)", validators=[DataRequired()]
    )
