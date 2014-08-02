import sys
import argparse

import alert_builder
import re
import buildbot
import urllib

import requests_cache
import collections

requests_cache.install_cache('explain')

URL_RE = re.compile('(?P<master_url>.*)/builders/(?P<builder_name>.*)/builds/(?P<build_number>\d+)/?')
# http://build.chromium.org/p/tryserver.chromium/buildstatus?builder=linux_rel&number=85072
BUILDSTATUS_RE = re.compile('(?P<master_url>.*)/buildstatus\?builder=(?P<builder_name>.*)&number=(?P<build_number>\d+)')


def main(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('urls_path', action='store')
    args = parser.parse_args(args)

    with open(args.urls_path) as url_file:
      urls = map(str.strip, url_file.readlines())

    # FIXME: HACK
    CACHE_PATH = '/src/build_cache'
    cache = buildbot.BuildCache(CACHE_PATH)

    counts = collections.Counter()

    for url in urls:
      match = URL_RE.match(url)
      if not match:
        match = BUILDSTATUS_RE.match(url)
      if not match:
        print url, 'URL REGEXP MATCH ERROR'
        counts.update(['URL REGEXP MATCH ERROR'])
        continue

      master_url = match.group('master_url')
      builder_name = urllib.unquote_plus(match.group('builder_name'))
      build_number = match.group('build_number')

      # Grab the build
      build = buildbot.fetch_build_json(cache, master_url, builder_name, build_number)
      if not build:
        print url, 'BUILD MISSING'
        counts.update(['BUILD MISSING'])
        continue

      _, failing, _ = alert_builder.complete_steps_by_type(build)

      if not failing:
        first_step = build['steps'][0]['results'] if build['steps'] else None
        message = 'NO FAILING STEPS? (first result: %s)' % (first_step)
        print url, message
        counts.update([message])
        continue

      step_names = [s['name'] for s in failing]
      print url, step_names

      counts.update(step_names)

    for key, count in counts.most_common():
      if count < 5:
        break
      print key, count


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))