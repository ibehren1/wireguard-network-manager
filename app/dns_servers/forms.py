from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import DataRequired


class DnsServerForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    ips = StringField(
        "DNS Server IPs (comma-separated)", validators=[DataRequired()]
    )
