from flask_wtf import FlaskForm
from wtforms import IntegerField, RadioField, SelectField, StringField
from wtforms.validators import DataRequired, NumberRange, Optional


class HostForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    endpoint = StringField("Endpoint (host:port, optional)", validators=[Optional()])
    listen_port = IntegerField(
        "Listen Port", validators=[Optional(), NumberRange(min=1, max=65535)]
    )
    dns_server_id = SelectField("DNS Server", validators=[Optional()])
    mtu = IntegerField("MTU (optional)", validators=[Optional(), NumberRange(min=576, max=9000)])


class HostCreateForm(HostForm):
    key_source = RadioField(
        "Key",
        choices=[
            ("generate", "Generate a new keypair"),
            ("provide", "Provide an existing keypair"),
            ("existing", "Use an existing unassigned key"),
        ],
        default="generate",
    )
    public_key = StringField("Public Key", validators=[Optional()])
    private_key = StringField("Private Key", validators=[Optional()])
    existing_key_id = SelectField("Unassigned Key", validators=[Optional()])


class AssignExistingKeyForm(FlaskForm):
    existing_key_id = SelectField("Unassigned Key", validators=[DataRequired()])


class NetworkMembershipForm(FlaskForm):
    network_id = SelectField("Network", validators=[DataRequired()])
    ip = StringField("IP Address", validators=[DataRequired()])


class HostPeerConnectionForm(FlaskForm):
    peer_host_id = SelectField("Peer Host", validators=[DataRequired()])
    network_id = SelectField("Shared Network", validators=[DataRequired()])
    allowed_ips_set_id = SelectField("Allowed IPs", validators=[DataRequired()])
    endpoint_override = StringField("Endpoint Override (optional)", validators=[Optional()])
    persistent_keepalive = IntegerField(
        "PersistentKeepalive (optional)", validators=[Optional(), NumberRange(min=1, max=3600)]
    )
