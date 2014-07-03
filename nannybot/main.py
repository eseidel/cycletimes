import webapp2

from google.appengine.ext import ndb


class AlertBlob(ndb.Model):
    date = ndb.DateTimeProperty(auto_now_add=True)
    content = ndb.StringProperty(indexed=False)


class Data(webapp2.RequestHandler):
    def get(self):
        query = AlertBlob.query().order(-AlertBlob.date)
        entries = query.fetch(1)
        if entries:
        	self.response.write(entries[0].content)
        else:
			self.response.write('[]')

    def post(self):
        alert = AlertBlob()
        alert.content = self.request.get('content')
        alert.put()


app = webapp2.WSGIApplication([
    ('/data', Data),
], debug=True)
