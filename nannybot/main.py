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
    # This could be JSONProperty, but no need to parse
    # the incoming json only to serialize it again.
    content = ndb.BlobProperty(indexed=False)


class IgnoreRule(ndb.Model):
    date = ndb.DateTimeProperty(auto_now_add=True)
    pattern = ndb.StringProperty(indexed=False)

    def dict_with_key(self):
        failure_dict = self.to_dict()
        failure_dict['key'] = self.key.id() # Should this be urlsafe?
        return failure_dict

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
        ignore_dicts = map(IgnoreRule.dict_with_key, query.fetch())
        self.response.headers.add_header("Access-Control-Allow-Origin", "*")
        self.response.headers['Content-Type'] = 'application/json'
        self.response.write(json.dumps(ignore_dicts, cls=DateTimeEncoder))

    def post(self):
        # FIXME: For whatever reason I can't get <form method='delete'> to work.
        if self.request.get('action') == 'delete':
            # FIXME: It's lame that this constructor is type-sensitive.
            key = ndb.Key(IgnoreRule, int(self.request.get('key')))
            key.delete()
        else:
            ignore = IgnoreRule()
            ignore.pattern = self.request.get('pattern')
            ignore.put()
        self.redirect('/')


class DataHandler(webapp2.RequestHandler):
    def get(self):
        query = AlertBlob.query().order(-AlertBlob.date)
        entries = query.fetch(1)
        response_json = {}
        if entries:
            response_json = json.loads(entries[0].content)
            ignores = IgnoreRule.query().fetch()

            def add_ignores(alert):
                alert['ignored_by'] = [ignore.key.id() for ignore in ignores if ignore.matches(alert)]
                return alert

            response_json.update({
                'date': entries[0].date,
                # FIXME: We should take an ignores param instead of always applying.
                'alerts': map(add_ignores, response_json['alerts']),
                'ignores': map(IgnoreRule.dict_with_key, ignores),
            })

        self.response.headers.add_header("Access-Control-Allow-Origin", "*")
        self.response.headers['Content-Type'] = 'application/json'
        self.response.write(json.dumps(response_json, cls=DateTimeEncoder, indent=1))

    def post(self):
        alert = AlertBlob()
        alert.content = self.request.get('content')
        alert.put()


app = webapp2.WSGIApplication([
    ('/data', DataHandler),
    ('/ignore', IgnoreHandler),
], debug=True)
