#!/usr/bin/env python
# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import datetime
import json
import logging
import operator
import os.path
import sys

import requests
import requests_cache

import analysis
import buildbot
import gatekeeper_extras
import reasons
import string_helpers

# This is relative to build/scripts:
# https://chromium.googlesource.com/chromium/tools/build/+/master/scripts
BUILD_SCRIPTS_PATH = "/src/build/scripts"
sys.path.append(BUILD_SCRIPTS_PATH)
from slave import gatekeeper_ng_config


CACHE_PATH = '/src/build_cache'


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

# FIXME: Pull from:
# https://chromium.googlesource.com/chromium/tools/build/+/master/scripts/slave/gatekeeper.json?format=TEXT
CONFIG_PATH = os.path.join(BUILD_SCRIPTS_PATH, 'slave', 'gatekeeper.json')

# Success or Warnings or None (didn't run) don't count as 'failing'.
NON_FAILING_RESULTS = (0, 1, None)


def compute_transition_and_failure_count(failure, failing_build, previous_builds):
  step_name = failure['step_name']
  reason = failure['reason']

  first_fail = failing_build
  last_pass = None
  fail_count = 1
  builds_missing_steps = []
  for build in previous_builds:
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
    log.warn("builds %s missing %s" % (string_helpers.re_range(builds_missing_steps), step_name))

  return last_pass, first_fail, fail_count


def complete_steps_by_type(build):
  # Some builders use a sub-step pattern which just generates noise.
  # FIXME: This code shouldn't contain constants like these.
  IGNORED_STEP_NAMES = ['steps', 'trigger', 'slave_steps']
  steps = build['steps']
  complete_steps = [s for s in steps if s['isFinished']]

  ignored = [s for s in complete_steps if s['name'] in IGNORED_STEP_NAMES]
  not_ignored = [s for s in complete_steps if s['name'] not in IGNORED_STEP_NAMES]

  # 'passing' and 'failing' are slightly inaccurate
  # 'not_failing' and 'not_passing' would be more accurate, but harder to read.
  passing = [s for s in not_ignored if s['results'][0] in NON_FAILING_RESULTS]
  failing = [s for s in not_ignored if s['results'][0] not in NON_FAILING_RESULTS]

  return passing, failing, ignored


def failing_steps_for_build(build):
  if build.get('results') is None:
    log.error('Bad build: %s %s %s' % (build.get('number'), build.get('eta'), build.get('currentStep', {}).get('name')))
  # This check is probably not necessary.
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


def alerts_from_step_failure(cache, step_failure, master_url, builder_name):
  build = buildbot.fetch_build_json(cache, master_url, builder_name, step_failure['build_number'])
  step = next((s for s in build['steps'] if s['name'] == step_failure['step_name']), None)
  step_template = {
    'master_url': master_url,
    'last_result_time': step['times'][1],
    'builder_name': builder_name,
    'last_failing_build': step_failure['build_number'],
    'step_name': step['name'],
    'latest_revisions': buildbot.revisions_from_build(build),
  }
  alerts = []
  reasons = reasons_for_failure(step, build, builder_name, master_url)
  if not reasons:
    alert = dict(step_template)
    alert['reason'] = None
    alerts.append(alert)
  else:
    for reason in reasons:
      alert = dict(step_template)
      alert['reason'] = reason
      alerts.append(alert)

  return alerts


# FIXME: This should merge with compute_transition_and_failure_count.
def fill_in_transition(cache, alert, recent_build_ids):
  previous_build_ids = [num for num in recent_build_ids if num < alert['last_failing_build']]
  fetch_function = lambda num: buildbot.fetch_build_json(cache, alert['master_url'], alert['builder_name'], num)
  build = fetch_function(alert['last_failing_build'])
  previous_builds = map(fetch_function, previous_build_ids)

  last_pass_build, first_fail_build, fail_count = \
    compute_transition_and_failure_count(alert, build, previous_builds)

  failing = buildbot.revisions_from_build(first_fail_build)
  passing = buildbot.revisions_from_build(last_pass_build) if last_pass_build else None

  alert.update({
    'failing_build_count': fail_count,
    'passing_build': last_pass_build['number'] if last_pass_build else None,
    'failing_build': first_fail_build['number'],
    'failing_revisions': failing,
    'passing_revisions': passing,
  })
  return alert


def find_current_step_failures(fetch_function, recent_build_ids):
  step_failures = []
  success_step_names = set()
  for build_id in recent_build_ids:
    build = fetch_function(build_id)
    passing, failing, _ = complete_steps_by_type(build)
    passing_names = set(map(lambda s: s['name'], passing))
    success_step_names.update(passing_names)
    for step in failing:
      if step['name'] in success_step_names:
        log.debug('%s passing in a more recent build, ignoring.' % (step['name']))
        continue
      print success_step_names
      step_failures.append({
        'build_number': build_id,
        'step_name': step['name'],
      })
    # Bad way to check is-finished.
    if build['eta'] is None:
      break
    log.debug('build %s incomplete, continuing search' % build['number'])
  return step_failures


# for each build:
# Walk all its completed steps
# if step in success_steps, continue.
# if step passed, add to success_steps
# if step failed, understand it.
# add failures to failures set.
# if build is finished, break.


def warm_build_cache(cache, master_url, builder_name, recent_build_ids, active_builds):
  actives = filter(lambda build: build['builderName'] == builder_name, active_builds)
  active_build_ids = [b['number'] for b in active_builds]
  # recent_build_ids includes active ones.
  finished_build_ids = [b for b in recent_build_ids if b not in active_build_ids]
  cache_key = buildbot.cache_key_for_build(master_url, builder_name, max(finished_build_ids))
  if not cache.get(cache_key):
    buildbot.prefill_builds_cache(cache, master_url, builder_name)


def alerts_for_builder(cache, master_url, builder_name, recent_build_ids):
  recent_build_ids = sorted(recent_build_ids, reverse=True)
  # Limit to 100 to match our current cache-warming logic
  recent_build_ids = recent_build_ids[:100]

  fetch_function = lambda num: buildbot.fetch_build_json(cache, master_url, builder_name, num)
  step_failures = find_current_step_failures(fetch_function, recent_build_ids)

  for failure in step_failures:
    print '%s from %s' % (failure['step_name'], failure['build_number'])

  alerts = []
  for step_failure in step_failures:
    alerts += alerts_from_step_failure(cache, step_failure, master_url, builder_name)
  return [fill_in_transition(cache, alert, recent_build_ids) for alert in alerts]


def alerts_for_master(cache, master_url, master_json):
  active_builds = []
  for slave in master_json['slaves'].values():
    for build in slave['runningBuilds']:
      active_builds.append(build)

  alerts = []
  for builder_name, builder_json in master_json['builders'].items():
    # cachedBuilds will include runningBuilds.
    recent_build_ids = builder_json['cachedBuilds']
    master_name = buildbot.master_name_from_url(master_url)
    log.debug("%s %s" % (master_name, builder_name))

    warm_build_cache(cache, master_url, builder_name, recent_build_ids, active_builds)
    alerts.extend(alerts_for_builder(cache, master_url, builder_name, recent_build_ids))

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


def apply_gatekeeper_rules(alerts, gatekeeper):
  filtered_alerts = []
  for alert in alerts:
    master_url = alert['master_url']
    master_name = buildbot.master_name_from_url(master_url)
    config = gatekeeper.get(master_url)
    if not config:
      # Unclear if this should be set or not?
      # alert['would_close_tree'] = False
      filtered_alerts.append(alert)
      continue
    excluded_builders = gatekeeper_extras.excluded_builders(config)
    if alert['builder_name'] in excluded_builders:
      continue
    alert['would_close_tree'] = \
      gatekeeper_extras.would_close_tree(config, alert['builder_name'], alert['step_name'])
    filtered_alerts.append(alert)
    alert['tree_name'] = gatekeeper_extras.tree_for_master(master_name)
  return filtered_alerts


def fetch_master_urls(gatekeeper, args):
  # Currently using gatekeeper.json, but could use:
  # https://apis-explorer.appspot.com/apis-explorer/?base=https://chrome-infra-stats.appspot.com/_ah/api#p/stats/v1/stats.masters.list?_h=1&
  master_urls = gatekeeper.keys()
  if args.master_filter:
    master_urls = [url for url in master_urls if args.master_filter not in url]
  return master_urls


def main(args):
  parser = argparse.ArgumentParser()
  parser.add_argument('data_url', action='store', nargs='*')
  parser.add_argument('--use-cache', action='store_true')
  parser.add_argument('--master-filter', action='store')
  args = parser.parse_args(args)

  if not args.data_url:
    log.warn("No /data url passed, won't do anything")

  if args.use_cache:
    requests_cache.install_cache('failure_stats')
  else:
    requests_cache.install_cache(backend='memory')

  gatekeeper = gatekeeper_ng_config.load_gatekeeper_config(CONFIG_PATH)
  master_urls = fetch_master_urls(gatekeeper, args)
  start_time = datetime.datetime.now()

  latest_revisions = {}

  cache = buildbot.BuildCache(CACHE_PATH)

  alerts = []
  for master_url in master_urls:
    master_json = buildbot.fetch_master_json(master_url)
    master_alerts = alerts_for_master(cache, master_url, master_json)
    alerts.extend(master_alerts)

    # FIXME: This doesn't really belong here. garden-o-matic wants
    # this data and we happen to have the builder json cached at
    # this point so it's cheap to compute.
    revisions = buildbot.latest_revisions_for_master(cache, master_url, master_json)
    latest_revisions.update(revisions)


  print "Fetch took: %s" % (datetime.datetime.now() - start_time)

  alerts = apply_gatekeeper_rules(alerts, gatekeeper)

  alerts = analysis.assign_keys(alerts)
  reason_groups = analysis.group_by_reason(alerts)
  range_groups = analysis.merge_by_range(reason_groups)
  data = { 'content': json.dumps({
      'alerts': alerts,
      'reason_groups': reason_groups,
      'range_groups': range_groups,
      'latest_revisions': latest_revisions,
  })}
  for url in args.data_url:
    log.info('POST %s alerts to %s' % (len(alerts), url))
    requests.post(url, data=data)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
