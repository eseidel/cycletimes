import sys
import argparse

import alert_builder
import re
import buildbot
import urllib

import requests_cache
import collections
import logging
import json

requests_cache.install_cache('explain')

URL_RE = re.compile('(?P<master_url>.*)/builders/(?P<builder_name>.*)/builds/(?P<build_number>\d+)/?')
# http://build.chromium.org/p/tryserver.chromium/buildstatus?builder=linux_rel&number=85072
BUILDSTATUS_RE = re.compile('(?P<master_url>.*)/buildstatus\?builder=(?P<builder_name>.*)&number=(?P<build_number>\d+)')


def jobs_from_urls(urls):
  jobs = []
  for url in urls:
    match = URL_RE.match(url)
    if not match:
      match = BUILDSTATUS_RE.match(url)
    if not match:
      logging.error('MATCH ERROR: %s' % (url))
      continue

    jobs.append({
      'master_url': match.group('master_url'),
      'builder_name': urllib.unquote_plus(match.group('builder_name')),
      'build_number': match.group('build_number'),
    })
  return jobs


def main(args):
  parser = argparse.ArgumentParser()
  parser.add_argument('urls_path', action='store')
  args = parser.parse_args(args)

  # FIXME: HACK
  CACHE_PATH = '/src/build_cache'
  cache = buildbot.BuildCache(CACHE_PATH)

  with open(args.urls_path) as url_file:
    jobs = jobs_from_urls(map(str.strip, url_file.readlines()))

  alerts = []
  for job in jobs:
    master_url = job['master_url']
    builder_name = job['builder_name']
    build_number = job['build_number']
    build = buildbot.fetch_build_json(cache, master_url, builder_name, build_number)
    if not build:
      continue

    _, failing, _ = alert_builder.complete_steps_by_type(build)

    if not failing:
      first_step = build['steps'][0]['results'] if build['steps'] else None
      logging.error('%s NO FAILING STEPS? (first result: %s)' % (job, first_step))
      continue

    issue_id = buildbot.property_from_build(build, 'issue')
    patchset_id = buildbot.property_from_build(build, 'patchset')
    slave_name = buildbot.property_from_build(build, 'slavename')

    alert_template = {
      'master_url': job['master_url'],
      'builder_name': job['builder_name'],
      'build_number': job['build_number'],
      'slave_name': slave_name,
      'issue_id': issue_id,
      'patchset_id': patchset_id,
      'start_time': int(build['times'][0]),
      'end_time': int(build['times'][1]),
    }

    for step in failing:
      reasons = alert_builder.reasons_for_failure(step, build, builder_name, master_url)
      # Hack to make alert creation simpler:
      if not reasons:
        reasons = [None]

      for reason in reasons:
        alert = alert_template.copy()
        alert.update({
          'step_name': step['name'],
          'reason': reason,
        })
        alerts.append(alert)

  counts = collections.Counter()
  for alert in alerts:
    key = alert['step_name']
    if alert['reason']:
      key += ':' + alert['reason']
    counts[key] += 1

  print json.dumps({
    'counts': counts.most_common(),
    'flakes': alerts,
    }, indent=1)

# Currently we're feeding this script with "flaky" try job urls
# which are collected by stats.py

# Alternatively we could just walk all try-builders and collect all builder
# but then we would need to collate builds based on issue_id.  This approach
# would allow showing "common" failures across all builders however
# before they would show up otherwise.


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))