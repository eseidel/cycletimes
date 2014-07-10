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

# This is relative to build/scripts:
# https://chromium.googlesource.com/chromium/tools/build/+/master/scripts
BUILD_SCRIPTS_PATH = "/src/build/scripts"
sys.path.append(BUILD_SCRIPTS_PATH)
from slave import gatekeeper_ng_config

import reasons


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


MASTER_URLS = ['https://build.chromium.org/p/chromium.webkit']

CONFIG_PATH = os.path.join(BUILD_SCRIPTS_PATH, 'slave', 'gatekeeper.json')
BUILDERS_URL = 'https://chrome-build-extract.appspot.com/get_master/%s'
BUILDS_URL = 'https://chrome-build-extract.appspot.com/get_builds'


# Success or Warnings or None (didn't run) don't count as 'failing'.
NON_FAILING_RESULTS = (0, 1, None)

# Always use parent_*_revision, it should be set correctly
# even for bots which don't have a 'parent' bot which builds
# for them.
REPOSITORIES = [
  {
    'name': 'chromium',
    'change_url': 'http://crrev.com/%s',
    'changelog_url': 'http://build.chromium.org/f/chromium/perf/dashboard/ui/changelog.html?url=/trunk&range=%s:%s',
    'buildbot_revision_name': 'got_revision',
  },
  {
    'name': 'blink',
    'change_url': 'https://src.chromium.org/viewvc/blink?view=revision&revision=%s',
    'changelog_url': 'http://build.chromium.org/f/chromium/perf/dashboard/ui/changelog_blink.html?url=/trunk&range=%s:%s',
    'buildbot_revision_name': 'got_webkit_revision',
  },
  {
    'name': 'v8',
    'change_url': 'https://code.google.com/p/v8/source/detail?r=%s',
    'changelog_url': 'http://build.chromium.org/f/chromium/perf/dashboard/ui/changelog_v8.html?url=/trunk&range=%s:%s',
    'buildbot_revision_name': 'got_v8_revision',
  },
  {
    'name': 'nacl',
    'change_url': 'http://src.chromium.org/viewvc/native_client?view=revision&revision=',
    # FIXME: Does nacl have a changelog viewer?
    'buildbot_revision_name': 'got_nacl_revision',
  },
  # Skia, for whatever reason, isn't exposed in the buildbot properties
  # so don't bother to include it here.
  # {
  #   'name': 'skia',
  #   'change_url': 'https://code.google.com/p/skia/source/detail?r=%s',
  #   'buildbot_revision_name': 'got_skia_revision',
  # }
]


def repository_by_name(name):
  for repository in REPOSITORIES:
    if repository['name'] == name:
      return repository


def master_name_from_url(master_url):
  return urlparse.urlparse(master_url).path.split('/')[-1]


def fetch_builder_names(master_url):
  url = BUILDERS_URL % master_name_from_url(master_url)
  request = requests.get(url)
  if request.elapsed.total_seconds() > 1:
    log.debug('Slow: %0.1fs %s' % (request.elapsed.total_seconds(), request.url))
  return request.json()['builders']


def builds_for_builder(master_url, builder_name):
  master_name = master_name_from_url(master_url)
  params = { 'master': master_name, 'builder': builder_name }
  request = requests.get(BUILDS_URL, params=params)
  if request.elapsed.total_seconds() > 1:
    log.warn('Slow: %0.1fs %s' % (request.elapsed.total_seconds(), request.url))
  return request.json()['builds']


# FIXME: This belongs in gatekeeper_ng_config.py
def excluded_builders(config):
    return config[0].get('*', {}).get('excluded_builders', set())


def revisions_from_build(build_json):
  def _property_value(build_json, property_name):
    for prop_tuple in build_json['properties']:
      if prop_tuple[0] == property_name:
        return prop_tuple[1]

  revisions = {}
  for repository in REPOSITORIES:
    buildbot_property = repository['buildbot_revision_name']
    # This is epicly stupid:  'tester' builders have the wrong
    # revision for 'got_foo_revision' and we have to use
    # parent_got_foo_revision instead, but non-tester builders
    # don't have the parent_ versions, so we have to fall back
    # to got_foo_revision in those cases!
    # Don't even think about using 'revision' that's wrong too.
    revision = _property_value(build_json, 'parent_' + buildbot_property)
    if not revision:
      revision = _property_value(build_json, buildbot_property)
    revisions[repository['name']] = revision
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


def compute_transition_and_failure_count(recent_builds, step_name, splitter,
    piece, build, builder_name, master_url):
  '''Returns last_pass_build, first_fail_build, fail_count'''
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
      if splitter and piece:
        pieces = splitter.split_step(step, build, builder_name, master_url)
        # This build doesn't seem to have this step reason, ignore it.
        if not pieces:
          continue
        # Failed, but passed our piece!
        # FIXME: This is wrong for compile failures, and possibly
        # for test failures as well if not all tests are run...
        if piece not in pieces:
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


# FIXME: This belongs in gatekeeper_ng_config.py
def would_close_tree(master_config, builder_name, step_name):
  # FIXME: Section support should be removed:
  master_config = master_config[0]
  builder_config = master_config.get(builder_name, {})
  if not builder_config:
    builder_config = master_config.get('*', {})

  # close_tree is currently unused in gatekeeper.json but planned to be.
  close_tree = builder_config.get('close_tree', True)
  if not close_tree:
    log.debug('close_tree is false')
    return False

  # Excluded steps never close.
  excluded_steps = set(builder_config.get('excluded_steps', []))
  if step_name in excluded_steps:
    log.debug('%s is an excluded_step' % step_name)
    return False

  # See gatekeeper_ng_config.py for documentation of
  # the config format.
  # forgiving/closing controls if mails are sent on close.
  # steps/optional controls if step-absence indicates failure.
  # this function assumes the step is present and failing
  # and thus doesn't care between these 4 types:
  closing_steps = (builder_config.get('forgiving_steps', set()) |
    builder_config.get('forgiving_optional', set()) |
    builder_config.get('closing_steps', set()) |
    builder_config.get('closing_optional', set()))

  # A '*' in any of the above types means it applies to all steps.
  if '*' in closing_steps:
    return True

  if step_name in closing_steps:
    return True

  log.debug('%s not in closing_steps: %s' % (step_name, closing_steps))
  return False


def alerts_for_builder(master_config, master_url, builder_name):
  alerts = []
  recent_builds = builds_for_builder(master_url, builder_name)
  if not recent_builds:
    log.warn("No recent builds for %s, skipping." % builder_name)
    return alerts

  build = recent_builds[0]
  # If we are not currently failing, we have no alerts to give.
  if build.get('results', 0) == 0:
    return alerts

  failing_steps = [step for step in build['steps'] if step['results'][0] not in NON_FAILING_RESULTS]

  # Some builders use a sub-step pattern which just generates noise.
  # FIXME: This code shouldn't contain constants like these.
  IGNORED_STEPS = ['steps', 'trigger', 'slave_steps']

  for step in failing_steps:
    if step['name'] in IGNORED_STEPS:
      continue

    would_close = would_close_tree(master_config, builder_name, step['name'])

    pieces = None
    splitter = next((splitter for splitter in reasons.STEP_SPLITTERS if splitter.handles_step(step)), None)
    if splitter:
      pieces = splitter.split_step(step, build, builder_name, master_url)

    if not pieces:
      pieces = [None] # FIXME: Lame hack.

    for piece in pieces:
      last_pass_build, first_fail_build, fail_count = \
        compute_transition_and_failure_count(recent_builds, step['name'],
          splitter, piece, build, builder_name, master_url)

      failing = revisions_from_build(first_fail_build)
      passing = revisions_from_build(last_pass_build) if last_pass_build else None

      alerts.append({
        'master_url': master_url,
        'last_result_time': step['times'][1],
        'builder_name': builder_name,
        'step_name': step['name'],
        'failing_build_count': fail_count,
        'passing_build': last_pass_build['number'] if last_pass_build else None,
        'failing_build': first_fail_build['number'],
        'failing_revisions': failing,
        'passing_revisions': passing,
        'piece': piece,
        'would_close_tree': would_close,
      })
  return alerts

def alerts_for_master(master_url, master_config):
  builder_names = fetch_builder_names(master_url)
  builder_names = sorted(set(builder_names) - excluded_builders(master_config))
  alerts = []
  for builder_name in builder_names:
    master_name = master_name_from_url(master_url)
    log.debug("%s %s" % (master_name, builder_name))
    alerts.extend(alerts_for_builder(master_config, master_url, builder_name))
  return alerts


def fetch_alerts(args, gatekeeper_config):
  alerts = []
  for url, config in gatekeeper_config.items():
    if args.master_filter and args.master_filter not in url:
      continue
    alerts.extend(alerts_for_master(url, config))
  return alerts


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

  gatekeeper_config = gatekeeper_ng_config.load_gatekeeper_config(CONFIG_PATH)
  alerts = fetch_alerts(args, gatekeeper_config)
  data = { 'content': json.dumps(alerts) }
  for url in args.data_url:
    log.info('POST %s alerts to %s' % (len(alerts), url))
    requests.post(url, data=data)

  # Find the list of failing steps?
  # Walk backwards until no failure.
  #

  # Find the list of bots who's most recent build had a compile failure.
  # Walk backwards until it didn't.
  # Know the regression range.
  # Apply heuristics.
  # Rollout.
  # for master_url in MASTER_URLS:
  #   master_config = gatekeeper_config[master_url]


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
