from flask_wtf import FlaskForm
from wtforms import RadioField, SelectField, StringField
from wtforms.validators import DataRequired, Optional


class KeyCreateForm(FlaskForm):
    key_source = RadioField(
        "Key",
        choices=[("generate", "Generate a new keypair"), ("provide", "Provide an existing keypair")],
        default="generate",
    )
    public_key = StringField("Public Key", validators=[Optional()])
    private_key = StringField("Private Key", validators=[Optional()])


class KeyAssignForm(FlaskForm):
    target = SelectField("Assign To", validators=[DataRequired()])
