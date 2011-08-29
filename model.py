from google.appengine.ext import db

class Profile(db.Model):
  email = db.EmailProperty(required=True)
  current_calendar = db.StringProperty(multiline=False, required=True)

class Settings(db.Model):
  name = db.StringProperty(multiline=False, required=True)
  value = db.StringProperty(multiline=False, required=True)