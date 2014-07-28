#!/usr/bin/env python
# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import datetime
import json
import logging
import os.path
import sys

import requests
import requests_cache

import analysis
import buildbot
import gatekeeper_extras
import reasons
import alert_builder


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
    master_alerts = alert_builder.alerts_for_master(cache, master_url, master_json)
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
