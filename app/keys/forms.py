from flask_wtf import FlaskForm
from wtforms import SelectField, StringField
from wtforms.validators import DataRequired, Optional


class KeyCreateForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    private_key = StringField("Private Key (optional)", validators=[Optional()])


class KeyAssignForm(FlaskForm):
    target = SelectField("Assign To", validators=[DataRequired()])
