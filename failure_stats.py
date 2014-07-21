#!/usr/bin/env python
# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import requests
import requests_cache
import collections
import urlparse
import argparse
import sys
import os

# This is relative to build/scripts:
# https://chromium.googlesource.com/chromium/tools/build/+/master/scripts
BUILD_SCRIPTS_PATH = "/src/build/scripts"
sys.path.append(BUILD_SCRIPTS_PATH)
from slave import gatekeeper_ng_config

CONFIG_PATH = os.path.join(BUILD_SCRIPTS_PATH, 'slave', 'gatekeeper.json')
BUILDERS_URL = 'https://chrome-build-extract.appspot.com/get_master/%s'
BUILDS_URL = 'https://chrome-build-extract.appspot.com/get_builds'


def fetch_builder_names(master_name):
  url = BUILDERS_URL % master_name
  return requests.get(url).json()['builders']


def builds_for_builder(master_name, builder_name):
  params = { 'master': master_name, 'builder': builder_name }
  return requests.get(BUILDS_URL, params=params).json()['builds']


def pretty_failure_name(failing_results):
  return ', '.join(map(' '.join, failing_results))[:30]


def print_worst_failures(name, outcomes):
  print '\n%s Top 10 failures:' % name.upper()
  for reason, count in outcomes.most_common(11):
    if reason == 'OK':
      continue
    print '%3s: %s' % (count, reason)


def main(args):
  parser = argparse.ArgumentParser()
  parser.add_argument('--use-cache', action='store_true')
  parser.add_argument('--builders', action='store_true')
  parser.add_argument('--master-filter', action='store')
  parser.add_argument('--noise-threshold', action='store', type=int, default=2)
  parser.add_argument('--build-limit', action='store', type=int, default=100)
  parser.add_argument('--show-pass', action='store_true')
  args = parser.parse_args(args)

  if args.use_cache:
    requests_cache.install_cache('failure_stats')

  gatekeeper_config = gatekeeper_ng_config.load_gatekeeper_config(CONFIG_PATH)

  all_outcomes = collections.Counter()

  for master_url, master_config in gatekeeper_config.items():
    master_outcomes = collections.Counter()
    master_name = urlparse.urlparse(master_url).path.split('/')[-1]
    if args.master_filter:
      if args.master_filter not in master_name:
        continue
    if args.builders:
      print
      print master_name
    common_config = master_config[0].get('*', {})
    excluded_builders = common_config.get('excluded_builders', set())
    builder_names = fetch_builder_names(master_name)
    builder_names = sorted(set(builder_names) - excluded_builders)
    for builder_name in builder_names:
      outcomes = collections.Counter()
      recent_builds = builds_for_builder(master_name, builder_name)
      for build in recent_builds[:args.build_limit]:
        if build.get('results', 0) == 0:
          continue
        failing_results = [step['results'][1] for step in build['steps']
          if step['results'][0]]
        failure_name = pretty_failure_name(failing_results)
        outcomes[failure_name] += 1
      if args.builders:
        fail_strings = ['{:2} {:<30}'.format(*reversed(tup)) for tup in outcomes.most_common(3) if tup[1] >= args.noise_threshold]
        if not outcomes.most_common(3):
          if args.show_pass:
            print '%30s : PASS' % builder_name
        elif outcomes.most_common(1)[0][1] < args.noise_threshold:
          print '%30s : noise' % builder_name
        else:
          print '%30s : %s' % (builder_name, ' | '.join(fail_strings))
      master_outcomes += outcomes

    print_worst_failures(master_name, master_outcomes)
    all_outcomes += master_outcomes

  if not args.master_filter:
    print_worst_failures('total for all %s masters' % CONFIG_PATH, all_outcomes)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
