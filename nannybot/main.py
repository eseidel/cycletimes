import webapp2
from google.appengine.ext import ndb
import json
import calendar
import datetime
import collections


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
        ignore_dicts = map(ndb.Model.to_dict, query.fetch())
        self.response.write(json.dumps(ignore_dicts, cls=DateTimeEncoder))

    def post(self):
        ignore = IgnoreRule()
        ignore.pattern = self.request.get('pattern')
        ignore.put()
        self.get()

def make_reason_groups(alerts):
    by_reason = collections.defaultdict(list)
    for alert in alerts:
        # FIXME: Probably want something smarter here.
        reason_key = alert['step_name']
        if alert['piece']:
            reason_key += ':%s' % alert['piece']
        by_reason[reason_key].append(alert)

    groups = []
    for reason_key, alerts in by_reason.items():
        groups.append({
            'reason_key': reason_key,
            # FIXME: These should probably be a list of keys
            # once alerts have keys.
            'failures': alerts,
        })
    return groups


class DataHandler(webapp2.RequestHandler):
    def get(self):
        query = AlertBlob.query().order(-AlertBlob.date)
        entries = query.fetch(1)
        response_json = {}
        if entries:
            alerts = json.loads(entries[0].content)
            ignores = IgnoreRule.query().fetch()

            def add_ignores(alert):
                alert['ignored_by'] = [ignore.key.id() for ignore in ignores if ignore.matches(alert)]
                return alert
            response_json = {
                'date': entries[0].date,
                'content': map(add_ignores, alerts),
                'ignores': map(IgnoreRule.dict_with_key, ignores),
                'reason_groups': make_reason_groups(alerts)
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
