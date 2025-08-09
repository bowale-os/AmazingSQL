from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, PasswordField, TextAreaField
from wtforms.validators import DataRequired, URL, Length, Email
from flask_wtf.file import FileField, FileAllowed
# from flask_ckeditor import CKEditorField


class SignUpForm(FlaskForm):
    first_name = StringField("Name", validators=[DataRequired(), Length(max=30, message="Name is too long")])
    last_name = StringField("Name", validators=[DataRequired(), Length(max=30, message="Name is too long")])
    email = StringField("Email: ", validators=[DataRequired(), Email(message="Please enter a valid email."), Length(max=40, message="Email is too long")])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=10, message="Password should be 10 characters or longer")])
    username = StringField('Username', validators=[DataRequired(), Length(max=15, message="Username should be 15 characters or less")])
    bio = TextAreaField("Your Bio: ", validators=[Length(max=200, message="bio is too long")])
    profile_pic = FileField("Profile picture", validators=[
         FileAllowed(['jpg', 'png', 'jpeg'], 'Images only!')
     ])
    submit = SubmitField('Join AmazingSQL')


class LogInForm(FlaskForm):
    email = StringField("Email: ", validators=[DataRequired(), Email(message="Please enter a valid email."), Length(max=40, message="Email is too long")])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=10, message="Password should be 10 characters or longer")])
    submit = SubmitField('Log in')

class BioForm(FlaskForm):
    bio = TextAreaField("Your Bio: ", validators=[DataRequired(message="Bio cannot be empty"), Length(max=200, message="bio is too long")])
    submit = SubmitField("Add bio")

class FeedbackForm(FlaskForm):
    like = TextAreaField("What did you enjoy?")
    dislike = TextAreaField("What did you dislike?")
    hate = TextAreaField("What confused or frustrated you?")

