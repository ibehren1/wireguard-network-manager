from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired, Optional


class NetworkForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    cidr = StringField("CIDR (e.g. 10.0.0.0/24)", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[Optional()])
