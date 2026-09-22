# Copyright © 2026 Isaac Behrens. All rights reserved.

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import HiddenField
from wtforms.validators import DataRequired


class RestoreUploadForm(FlaskForm):
    archive = FileField("Backup archive (.zip)", validators=[FileRequired()])


class ConfirmRestoreForm(FlaskForm):
    source = HiddenField(validators=[DataRequired()])
