import webapp2
from google.appengine.ext import ndb
import json
import calendar
import datetime


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        # FIXME: This should be UTC.
        if isinstance(obj, datetime.datetime):
            return calendar.timegm(obj.timetuple())
        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)


class AlertBlob(ndb.Model):
    date = ndb.DateTimeProperty(auto_now_add=True)
    content = ndb.StringProperty(indexed=False)


class IgnoreRule(ndb.Model):
    date = ndb.DateTimeProperty(auto_now_add=True)
    pattern = ndb.StringProperty(indexed=False)

    def matches(self, failure_dict):
        pieces = self.pattern.split('=')
        if len(pieces) != 2:
            return False
        key, value = pieces
        if not key or not value:
            return False
        return value in failure_dict.get(key, '')


class IgnoreHandler(webapp2.RequestHandler):
    def get(self):
        query = IgnoreRule.query()
        ignore_dicts = map(ndb.Model.to_dict, query.fetch())
        self.response.write(json.dumps(ignore_dicts, cls=DateTimeEncoder))

    def post(self):
        ignore = IgnoreRule()
        ignore.pattern = self.request.get('pattern')
        ignore.put()
        self.get()


class DataHandler(webapp2.RequestHandler):
    def get(self):
        query = AlertBlob.query().order(-AlertBlob.date)
        entries = query.fetch(1)
        response_json = {}
        if entries:
            alerts = json.loads(entries[0].content)
            ignores = IgnoreRule.query().fetch()
            is_ignored = lambda alert: any(ignore.matches(alert) for ignore in ignores)
            response_json = {
                'date': entries[0].date,
                'content': filter(lambda alert: not is_ignored(alert), alerts),
                'ignores': map(ndb.Model.to_dict, ignores),
            }
        self.response.write(json.dumps(response_json, cls=DateTimeEncoder))

    def post(self):
        alert = AlertBlob()
        alert.content = self.request.get('content')
        alert.put()


app = webapp2.WSGIApplication([
    ('/data', DataHandler),
    ('/ignore', IgnoreHandler),
], debug=True)
