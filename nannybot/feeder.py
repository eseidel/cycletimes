#!/usr/bin/env python
# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import collections
import itertools
import json
import logging
import operator
import os.path
import requests
import requests_cache
import sys
import urllib
import urlparse
import gatekeeper_extras

import pipeline

import gatekeeper_ng_config

import reasons

from google.appengine.api import urlfetch

# Masters:
# https://apis-explorer.appspot.com/apis-explorer/?base=https://chrome-infra-stats.appspot.com/_ah/api#p/stats/v1/stats.masters.list?_h=1&


# Python logging is stupidly verbose to configure.
def setup_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger, handler


log, logging_handler = setup_logging()

GATEKEEPER_CONFIG = 'gatekeeper.json'

# Success or Warnings or None (didn't run) don't count as 'failing'.
NON_FAILING_RESULTS = (0, 1, None)


def master_name_from_url(master_url):
  return urlparse.urlparse(master_url).path.split('/')[-1]


def master_json_url(master_url):
  builders_url = 'https://chrome-build-extract.appspot.com/get_master/%s'
  return builders_url % master_name_from_url(master_url)


def fetch_master_json(master_url):
  url = master_json_url(master_url)
  response = urlfetch.fetch(url, follow_redirects=False)
  return json.loads(response.content)


def builds_for_builder(master_url, builder_name):
  builds_url = 'https://chrome-build-extract.appspot.com/get_builds'
  master_name = master_name_from_url(master_url)
  params = { 'master': master_name, 'builder': builder_name }
  url = "%s?%s" % (builds_url, urllib.urlencode(params))
  response = urlfetch.fetch(url, follow_redirects=False)
  return json.loads(response.content)['builds']


# This effectively extracts the 'configuration' of the build
# we could extend this beyond repo versions in the future.
def revisions_from_build(build_json):
  def _property_value(build_json, property_name):
    for prop_tuple in build_json['properties']:
      if prop_tuple[0] == property_name:
        return prop_tuple[1]

  REVISION_VARIABLES = [
    ('chromium', 'got_revision'),
    ('blink', 'got_webkit_revision'),
    ('v8', 'got_v8_revision'),
    ('nacl', 'got_nacl_revision'),
    # Skia, for whatever reason, isn't exposed in the buildbot properties so
    # don't bother to include it here.
  ]

  revisions = {}
  for repo_name, buildbot_property in REVISION_VARIABLES:
    # This is epicly stupid:  'tester' builders have the wrong
    # revision for 'got_foo_revision' and we have to use
    # parent_got_foo_revision instead, but non-tester builders
    # don't have the parent_ versions, so we have to fall back
    # to got_foo_revision in those cases!
    # Don't even think about using 'revision' that's wrong too.
    revision = _property_value(build_json, 'parent_' + buildbot_property)
    if not revision:
      revision = _property_value(build_json, buildbot_property)
    revisions[repo_name] = revision
  return revisions


# http://stackoverflow.com/questions/9470611/how-to-do-an-inverse-range-i-e-create-a-compact-range-based-on-a-set-of-numb/9471386#9471386
def re_range(lst):
    def sub(x):
        return x[1] - x[0]

    ranges = []
    for k, iterable in itertools.groupby(enumerate(sorted(lst)), sub):
         rng = list(iterable)
         if len(rng) == 1:
             s = str(rng[0][1])
         else:
             s = "%s-%s" % (rng[0][1], rng[-1][1])
         ranges.append(s)
    return ', '.join(ranges)


def compute_transition_and_failure_count(failure, build, recent_builds):
  '''Returns last_pass_build, first_fail_build, fail_count'''

  step_name = failure['step_name']
  reason = failure['reason']

  first_fail = recent_builds[0]
  last_pass = None
  fail_count = 1
  builds_missing_steps = []
  for build in recent_builds[1:]:
    matching_steps = [s for s in build['steps'] if s['name'] == step_name]
    if len(matching_steps) != 1:
      if not matching_steps:
        # This case is pretty common, so just warn all at once at the end.
        builds_missing_steps.append(build['number'])
      else:
        log.error("%s has unexpected number of %s steps: %s" % (build['number'], step_name, matching_steps))
      continue

    step = matching_steps[0]
    step_result = step['results'][0]
    if step_result not in NON_FAILING_RESULTS:
      if reason:
        reasons = reasons_for_failure(step, build,
          failure['builder_name'], failure['master_url'])
        # This build doesn't seem to have this step reason, ignore it.
        if not reasons:
          continue
        # Failed, but our failure reason wasn't present!
        # FIXME: This is wrong for compile failures, and possibly
        # for test failures as well if not all tests are run...
        if reason not in reasons:
          break

      first_fail = build
      fail_count += 1
      continue

    # None is 'didn't run', not a passing result.
    if step_result is None:
      continue

    last_pass = build
    break

  if builds_missing_steps:
    log.warn("builds %s missing %s" % (re_range(builds_missing_steps), step_name))

  return last_pass, first_fail, fail_count


def failing_steps_for_build(build):
  # This check is probably not neccessy.
  if build.get('results', 0) == 0:
    return []

  failing_steps = [step for step in build['steps'] if step['results'][0] not in NON_FAILING_RESULTS]

  # Some builders use a sub-step pattern which just generates noise.
  # FIXME: This code shouldn't contain constants like these.
  IGNORED_STEPS = ['steps', 'trigger', 'slave_steps']
  return [step for step in failing_steps if step['name'] not in IGNORED_STEPS]


def reasons_for_failure(step, build, builder_name, master_url):
    splitter = next((splitter for splitter in reasons.STEP_SPLITTERS if splitter.handles_step(step)), None)
    if not splitter:
      return None
    return splitter.split_step(step, build, builder_name, master_url)


def failures_for_build(build, master_url, builder_name):
  failures = []
  for step in failing_steps_for_build(build):
    step_template = {
      'master_url': master_url,
      'last_result_time': step['times'][1],
      'builder_name': builder_name,
      'step_name': step['name'],
    }
    reasons = reasons_for_failure(step, build, builder_name, master_url)
    if not reasons:
      failure = dict(step_template)
      failure['reason'] = None
      failures.append(failure)
    else:
      for reason in reasons:
        failure = dict(step_template)
        failure['reason'] = reason
        failures.append(failure)

  return failures


# FIXME: This should merge with compute_transition_and_failure_count.
def fill_in_transition(failure, build, recent_builds):
  last_pass_build, first_fail_build, fail_count = \
    compute_transition_and_failure_count(failure, build, recent_builds)

  failing = revisions_from_build(first_fail_build)
  passing = revisions_from_build(last_pass_build) if last_pass_build else None

  failure.update({
    'failing_build_count': fail_count,
    'passing_build': last_pass_build['number'] if last_pass_build else None,
    'failing_build': first_fail_build['number'],
    'failing_revisions': failing,
    'passing_revisions': passing,
  })
  return failure


def alerts_for_builder(master_url, builder_name, active_builds):
  active_builds = filter(lambda build: build['builderName'] == builder_name, active_builds)

  recent_builds = builds_for_builder(master_url, builder_name)
  if not recent_builds:
    log.warn("No recent builds for %s, skipping." % builder_name)
    return []

  build = recent_builds[0]
  failures = failures_for_build(build, master_url, builder_name)
  return [fill_in_transition(failure, build, recent_builds) for failure in failures]


def alerts_for_master(master_url, master_config):
  master_json = fetch_master_json(master_url)

  builder_names = master_json['builders']
  builder_names = sorted(set(builder_names) \
    - gatekeeper_extras.excluded_builders(master_config))

  active_builds = []
  for slave in master_json['slaves'].values():
    for build in slave['runningBuilds']:
      active_builds.append(build)

  alerts = []
  for builder_name in builder_names:
    master_name = master_name_from_url(master_url)
    log.debug("%s %s" % (master_name, builder_name))
    alerts.extend(alerts_for_builder(master_url, builder_name, active_builds))

  for alert in alerts:
    alert['would_close_tree'] = \
      gatekeeper_extras.would_close_tree(master_config,
        alert['builder_name'], alert['step_name'])

  return alerts


def fetch_alerts(args, gatekeeper_config):
  alerts = []
  for url, config in gatekeeper_config.items():
    if args.master_filter and args.master_filter not in url:
      continue
    alerts.extend(alerts_for_master(url, config))
  return alerts


# Want to get all failures for all builds in the universe.
# Sort into most recent failures and then walk backwards to understand.

# cron job loads gatekeeper.json and starts MR with master_urls
# Map master_url to master_blob
# Map master_blob to (master:builder, build_blobs) and (master:builder, builder_url)
# Map builder_url to build_blobs
# Map build_blob to failures
# Shuffle failures into (master:builder, [failure, failure])
# Reduce

class FetchJson(pipeline.Pipeline):
  def run(self, url):
    return reqeusts.get(url).json()


class BuilderJob(pipeline.Pipeline):
  output_names = ['failures']

  def run(self, master_url, builder_name):
    recent_builds = builds_for_builder(master_url, builder_name)
    # if not recent_builds:
    #   log.warn("No recent builds for %s, skipping." % builder_name)
    #   return []

    # build = recent_builds[0]
    # failures = failures_for_build(build, master_url, builder_name)
    # return [fill_in_transition(failure, build, recent_builds) for failure in failures]

    self.fill(self.outputs.failures, [])


class MasterJob(pipeline.Pipeline):
  # output_names = ['builder_jobs']

  def run(self, master_url):
    master_json = fetch_master_json(master_url)
    builder_jobs = []
    for builder_name in master_json['builders']:
      # Get active jobs.
      builder_jobs.append( (yield BuilderJob(master_url, builder_name)) )


class MainPipeline(pipeline.Pipeline):
  def run(self):
    gatekeeper = gatekeeper_ng_config.load_gatekeeper_config('gatekeeper.json')
    master_urls = gatekeeper.keys()
    master_jobs = []
    for url in master_urls:
      master_jobs.append( (yield MasterJob(url)) )


  def finalized(self):
    if not self.was_aborted:
      logging.info('All done! Found %s results', self.outputs.default.value)


def main(args):
  parser = argparse.ArgumentParser()
  parser.add_argument('data_url', action='store', nargs='*')
  parser.add_argument('--use-cache', action='store_true')
  parser.add_argument('--master-filter', action='store')
  args = parser.parse_args(args)

  if args.use_cache:
    requests_cache.install_cache('failure_stats')
  else:
    requests_cache.install_cache(backend='memory')

  gatekeeper_config = gatekeeper_ng_config.load_gatekeeper_config('gatekeeper.json')
  alerts = fetch_alerts(args, gatekeeper_config)
  data = { 'content': json.dumps(alerts) }
  for url in args.data_url:
    log.info('POST %s alerts to %s' % (len(alerts), url))
    requests.post(url, data=data)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
