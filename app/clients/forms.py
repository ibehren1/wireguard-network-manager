from flask_wtf import FlaskForm
from wtforms import IntegerField, RadioField, SelectField, StringField
from wtforms.validators import DataRequired, NumberRange, Optional


class ClientForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    dns_server_id = SelectField("DNS Server", validators=[Optional()])


class ClientCreateForm(ClientForm):
    key_source = RadioField(
        "Key",
        choices=[
            ("new", "Generate a new key (or provide a private key below)"),
            ("existing", "Use an existing key"),
        ],
        default="new",
    )
    private_key = StringField("Private Key (optional)", validators=[Optional()])
    existing_key_id = SelectField("Existing Key", validators=[Optional()])


class AssignExistingKeyForm(FlaskForm):
    existing_key_id = SelectField("Existing Key", validators=[DataRequired()])


class ClientConnectionForm(FlaskForm):
    host_id = SelectField("Host", validators=[DataRequired()])
    network_id = SelectField("Shared Network", validators=[DataRequired()])
    allowed_ips_set_id = SelectField("Allowed IPs", validators=[DataRequired()])
    persistent_keepalive = IntegerField(
        "PersistentKeepalive", validators=[Optional(), NumberRange(min=1, max=3600)], default=10
    )
