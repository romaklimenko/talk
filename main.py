import os
import time

import atom

import gdata.calendar.client
import gdata.gauth

from google.appengine.api import users, xmpp
from google.appengine.ext import db, webapp
from google.appengine.ext.webapp import template, xmpp_handlers
from google.appengine.ext.webapp.util import run_wsgi_app, login_required

from model import Profile
from settings import DEBUG, SETTINGS

settings_gql = "SELECT * FROM Settings WHERE name = :1"

try:
  SETTINGS['APP_NAME'] = db.GqlQuery(settings_gql, "APP_NAME").get().value
  SETTINGS['CONSUMER_SECRET'] = db.GqlQuery(settings_gql, "CONSUMER_SECRET").get().value
  SETTINGS['CONSUMER_KEY'] = db.GqlQuery(settings_gql, "CONSUMER_KEY").get().value
  gcal = gdata.calendar.client.CalendarClient(source = SETTINGS['APP_NAME'])
except:
  pass

class MainPage(webapp.RequestHandler):
  def get(self):
    template_values = {}

    template_values['content'] = """<h2><a href="/authentification">Authentificate</a></h2>"""
    
    path = os.path.join(os.path.dirname(__file__), 'templates/index.html')
    
    self.response.out.write(template.render(path, template_values, DEBUG))

class Authentification(webapp.RequestHandler):
  @login_required
  def get(self):
    template_values = {}
    current_user = users.get_current_user()
    
    scopes = SETTINGS['SCOPES']
    oauth_callback = 'http://%s/authorization' % self.request.host
    consumer_key = SETTINGS['CONSUMER_KEY']
    consumer_secret = SETTINGS['CONSUMER_SECRET']
    
    request_token = gcal.get_oauth_token(scopes,
                                         oauth_callback,
                                         consumer_key,
                                         consumer_secret)
    # Persist this token in the datastore.
    request_token_key = 'request_token_%s' % current_user.email()
    gdata.gauth.ae_save(request_token, request_token_key)
    
    # Generate the authorization URL.
    approval_page_url = request_token.generate_authorization_url()
    message = """<h2><a href="%s">Request token for the Google Calendar Scope</a></h2>"""
    template_values['content'] = message % approval_page_url
    
    path = os.path.join(os.path.dirname(__file__), 'templates/index.html')
    self.response.out.write(template.render(path, template_values, DEBUG))

class Authorization(webapp.RequestHandler):
  @login_required
  def get(self):
    #template_values = {}
    current_user = users.get_current_user()
    
    # Remember the token that we stashed? Let's get it back from
    # datastore now and adds information to allow it to become an
    # access token.
    request_token_key = 'request_token_%s' % current_user.email()
    request_token = gdata.gauth.ae_load(request_token_key)
    gdata.gauth.authorize_request_token(request_token, self.request.uri)
    
    # We can now upgrade our authorized token to a long-lived
    # access token by associating it with gcal client, and
    # calling the get_access_token method.
    gcal.auth_token = gcal.get_access_token(request_token)
    
    # Note that we want to keep the access token around, as it
    # will be valid for all API calls in the future until a user
    # revokes our access. For example, it could be populated later
    # from reading from the datastore or some other persistence
    # mechanism.
    access_token_key = 'access_token_%s' % current_user.email()
    gdata.gauth.ae_save(request_token, access_token_key)
    
    feed = gcal.GetAllCalendarsFeed()
    
    for i, a_calendar in enumerate(feed.entry):
      self.response.out.write('\t%s. %s' % (i, a_calendar.title.text,))

class XmppHandler(xmpp_handlers.CommandHandler):
  def text_message(self, message):
    access_token_key = 'access_token_%s' % message.sender.partition('/')[0]
    access_token = gdata.gauth.ae_load(access_token_key)
    gcal.auth_token = access_token
    
    if message.body.startswith('.'):
      current_calendar = message.body.lstrip('.').strip()
      feed = gcal.GetOwnCalendarsFeed()
      gdata.calendar.data.CalendarEntry
      for i, a_calendar in enumerate(feed.entry):
        if a_calendar.title.text == current_calendar:
          query = db.GqlQuery(
                              "SELECT * FROM Profile WHERE email = :1", 
                              message.sender.partition('/')[0])
          profiles = query.fetch(100)
          for profile in profiles:
            profile.current_calendar = current_calendar
            profile.save()
            xmpp.send_message(
                              jids=message.sender, 
                              body='Current calendar switched to %s' % profile.current_calendar, 
                              from_jid="xmpptalk@appspot.com")
            return

          profile = Profile(
                            email = message.sender.partition('/')[0],
                            current_calendar = current_calendar)
          profile.put()
          xmpp.send_message(
                            jids=message.sender, 
                            body='Current calendar switched to %s' % profile.current_calendar, 
                            from_jid="xmpptalk@appspot.com")
          return
      xmpp.send_message(jids=message.sender, body='calendar not found', from_jid="xmpptalk@appspot.com")
      return

    format = '%Y-%m-%dT%H:%M:%S.000Z'

    start_time = time.gmtime()
    end_time = time.gmtime(time.time() + 3600)
    
    str_start_time = time.strftime(format, start_time)
    str_end_time = time.strftime(format, end_time)
    
    prev_event_end_time = time.gmtime(time.time() - 60)

    profile = Profile.all().filter("email =", message.sender.partition('/')[0]).get()
    
    event = gdata.calendar.data.CalendarEventEntry()
    event.title = atom.data.Title(text=message.body)
    event.content = atom.data.Content(text="createdby:talk")
    event.when.append(gdata.calendar.data.When(start=str_start_time, end=str_end_time))

    own_calendars_feed = gcal.GetOwnCalendarsFeed()    
    if (profile is None):
      event = gcal.InsertEvent(event)
    else:
      for i, a_calendar in enumerate(own_calendars_feed.entry):
        if (profile.current_calendar == a_calendar.title.text):
          calendar_id = a_calendar.link[0].href
          calendar_event_feed = gcal.get_calendar_event_feed(uri=calendar_id)
          event = gcal.InsertEvent(event, insert_uri=calendar_id)
    
    for when in event.when:
      str_start_time = when.start
      str_end_time = when.end

    query = gdata.calendar.client.CalendarEventQuery()
    query.start_max = str_start_time
    
    #fix latest event
    for i, a_calendar in enumerate(own_calendars_feed.entry):
      calendar_id = a_calendar.link[0].href
      calendar_event_feed = gcal.get_calendar_event_feed(uri=calendar_id, q=query)
    
      for i, an_event in enumerate(calendar_event_feed.entry):
        for a_when in an_event.when:
          if a_when.end >= str_start_time and an_event.content.text is not None and "createdby:talk" in an_event.content.text:
            try:
              a_when.end = time.strftime(format, prev_event_end_time)
              gcal.Update(an_event)
            except:
              continue

    xmpp.send_message(jids=message.sender, body=message.body, from_jid="xmpptalk@appspot.com")      
        
  """Handler class for all XMPP activity."""
  def c_command(self, message=None):
    access_token_key = 'access_token_%s' % message.sender.partition('/')[0]
    access_token = gdata.gauth.ae_load(access_token_key)
    gcal.auth_token = access_token
    
    calendars = ''
    
    feed = gcal.GetOwnCalendarsFeed()

    for i, a_calendar in enumerate(feed.entry):
      calendars = calendars + '\t%s. %s' % (i, a_calendar.link[0].href) #a_calendar.title.text,)
    
    xmpp.send_message(jids=message.sender, body=calendars, from_jid="xmpptalk@appspot.com")

application = webapp.WSGIApplication([
                                      ('/', MainPage),
                                      ('/authentification', Authentification),
                                      ('/authorization', Authorization),
                                      ('/_ah/xmpp/message/chat/', XmppHandler)
                                      ],
                                     debug=DEBUG)

def main():
  run_wsgi_app(application)