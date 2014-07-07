import webapp2

from google.appengine.ext import ndb
import json
import calendar

class AlertBlob(ndb.Model):
    date = ndb.DateTimeProperty(auto_now_add=True)
    content = ndb.StringProperty(indexed=False)


class Data(webapp2.RequestHandler):
    def get(self):
        query = AlertBlob.query().order(-AlertBlob.date)
        entries = query.fetch(1)
        response_json = {
            'date': calendar.timegm(entries[0].date.timetuple()) if entries else None,
            'content': json.loads(entries[0].content) if entries else None,
        }
        self.response.write(json.dumps(response_json))

    def post(self):
        alert = AlertBlob()
        alert.content = self.request.get('content')
        alert.put()


app = webapp2.WSGIApplication([
    ('/data', Data),
], debug=True)
