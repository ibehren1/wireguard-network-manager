# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField, StringField
from wtforms.validators import DataRequired, NumberRange, Optional

# NOTE: an interface's network_type constraint (see app/hosts/routes.py
# INTERFACE_TYPE_NETWORK_TYPE) is enforced server-side; this choices list only
# drives the select + the client-side filtering JS in interface_form.html.
INTERFACE_TYPE_CHOICES = [
    ("p2p", "P2P"),
    ("client", "Client"),
    ("client_non_wg", "Client (non-WireGuard)"),
]

INTERFACE_TYPE_LABELS = dict(INTERFACE_TYPE_CHOICES)


class HostForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])


class AssignExistingKeyForm(FlaskForm):
    existing_key_id = SelectField("Existing Key", validators=[DataRequired()])


class NetworkMembershipForm(FlaskForm):
    """Used by app.clients.routes for a Client's (host-field-free) network
    membership add form — do not add Host-only fields here, see InterfaceForm
    below for that."""

    network_id = SelectField("Network", validators=[DataRequired()])
    ip = StringField("IP Address", validators=[DataRequired()])
    interface_name = StringField("Interface Name", validators=[DataRequired()])


class InterfaceForm(NetworkMembershipForm):
    """A Host's network membership, now modeling one full WireGuard (or
    non-WireGuard) interface: network_id/ip/interface_name inherited from
    NetworkMembershipForm, plus per-interface type + config fields."""

    interface_type = SelectField(
        "Type", choices=INTERFACE_TYPE_CHOICES, validators=[DataRequired()]
    )
    hostname = StringField("Hostname or IP", validators=[Optional()])
    listen_port = IntegerField(
        "Listen Port", validators=[Optional(), NumberRange(min=1, max=65535)]
    )
    dns_server_id = SelectField("DNS Server", validators=[Optional()])
    mtu = IntegerField("MTU (optional)", validators=[Optional(), NumberRange(min=576, max=9000)])


class HostPeerConnectionForm(FlaskForm):
    peer_host_id = SelectField("Peer Host", validators=[DataRequired()])
    network_id = SelectField("Shared Network", validators=[DataRequired()])
    allowed_ips_set_id = SelectField("Allowed IPs", validators=[DataRequired()])
    endpoint_override = StringField("Endpoint Override (optional)", validators=[Optional()])
    persistent_keepalive = IntegerField(
        "PersistentKeepalive (optional)", validators=[Optional(), NumberRange(min=1, max=3600)]
    )
