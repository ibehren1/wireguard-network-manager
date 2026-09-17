from flask_wtf import FlaskForm
from wtforms import IntegerField, RadioField, SelectField, StringField
from wtforms.validators import DataRequired, NumberRange, Optional


class ClientForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    dns = StringField("DNS (optional)", validators=[Optional()])


class ClientCreateForm(ClientForm):
    key_source = RadioField(
        "Key",
        choices=[("generate", "Generate a new keypair"), ("provide", "Provide an existing keypair")],
        default="generate",
    )
    public_key = StringField("Public Key", validators=[Optional()])
    private_key = StringField("Private Key", validators=[Optional()])


class ClientConnectionForm(FlaskForm):
    host_id = SelectField("Host", validators=[DataRequired()])
    network_id = SelectField("Shared Network", validators=[DataRequired()])
    allowed_ips = StringField("AllowedIPs", validators=[DataRequired()])
    persistent_keepalive = IntegerField(
        "PersistentKeepalive", validators=[Optional(), NumberRange(min=1, max=3600)], default=10
    )
