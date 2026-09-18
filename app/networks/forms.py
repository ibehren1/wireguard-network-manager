from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Optional

NETWORK_TYPE_CHOICES = [
    ("p2p", "P2P"),
    ("ipam", "IPAM CIDR Space"),
    ("host_network", "Host Network"),
]

NETWORK_TYPE_LABELS = dict(NETWORK_TYPE_CHOICES)


class NetworkForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    cidr = StringField("CIDR (e.g. 10.0.0.0/24)", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[Optional()])
    network_type = SelectField(
        "Type", choices=NETWORK_TYPE_CHOICES, validators=[DataRequired()]
    )
    managing_host_id = SelectField("Managing Host", validators=[Optional()])
