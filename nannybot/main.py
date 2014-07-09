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


# Git ready, but only implemented for SVN atm.
def ids_after_first_including_second(first, second):
    return range(int(first) + 1, int(second) + 1)


# Git ready, but only implemented for SVN atm.
def is_ancestor_of(older, younger):
    return int(older) < int(younger)


def is_decendant_of(younger, older):
    return is_ancestor_of(older, younger)


def flatten_to_commit_list(passing, failing):
    # Flatten two commit dicts to a list of 'name:commit'
    if not passing or not failing:
        return None
    all_commits = []
    for name in passing.keys():
        commits = ids_after_first_including_second(passing[name], failing[name])
        all_commits.extend(['%s:%s' % (name, commit) for commit in commits])
    return all_commits


def lookup_and_compare(existing, new, compare):
    if not existing or compare(existing, new):
        return new
    return existing


# FIXME: Perhaps this would be cleaner as:
# passing = find_maximal(alert, 'passing_revisions', is_ancestor_of)
# failing = find_maximal(alert, 'failing_revisions', is_decendant_of)
def merge_regression_ranges(alerts):
    last_passing = {}
    first_failing = {}
    for alert in alerts:
        passing = alert['passing_revisions']
        if not passing:
            continue
        failing = alert['failing_revisions']
        for name in passing.keys():
            last_passing[name] = lookup_and_compare(last_passing.get(name), passing.get(name), is_ancestor_of)
            first_failing[name] = lookup_and_compare(first_failing.get(name), failing.get(name), is_decendant_of)
    return last_passing, first_failing


def reason_key_for_alert(alert):
    # FIXME: May need something smarter for reason_key.
    reason_key = alert['step_name']
    if alert['piece']:
        reason_key += ':%s' % alert['piece']
    return reason_key


def make_reason_groups(alerts):
    by_reason = collections.defaultdict(list)
    for alert in alerts:
        by_reason[reason_key_for_alert(alert)].append(alert)

    groups = []
    for reason_key, alerts in by_reason.items():
        last_passing, first_failing = merge_regression_ranges(alerts)
        blame_list = flatten_to_commit_list(last_passing, first_failing)
        groups.append({
            'reason_key': reason_key,
            'likely_revisions': blame_list,
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
