# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import sys
import urllib
import requests
import urlparse
import json

# This is relative to build/scripts:
# https://chromium.googlesource.com/chromium/tools/build/+/master/scripts
BUILD_SCRIPTS_PATH = "/src/build/scripts"
sys.path.append(BUILD_SCRIPTS_PATH)
from common import gtest_utils


# These are reason finders, more than splitters?
class GTestSplitter(object):
  def handles_step(self, step):
    GTEST_STEPS = [
      'browser_tests',
      'androidwebview_instrumentation_tests',
    ]
    # Probably anything ending in _tests (except webkit_tests) is gtest.
    return step['name'] in GTEST_STEPS

  def split_step(self, step, build, builder_name, master_url):
    # FIXME: Should get this from the step in some way?
    quoted_name = urllib.pathname2url(builder_name)
    args = (master_url, quoted_name, build['number'])
    build_url = "%s/builders/%s/builds/%s" % args
    stdio_url = "%s/steps/%s/logs/stdio" % (build_url, step['name'])

    log_parser = gtest_utils.GTestLogParser()
    results = requests.get(stdio_url)
    for line in results.text:
      log_parser.ProcessLine(line)

    failed_tests = log_parser.FailedTests()
    if failed_tests:
      return failed_tests
    # Failed to split, just group with the general failures.
    return None


def decode_results(results, include_expected=False):
    tests = convert_trie_to_flat_paths(results['tests'])
    failures = {}
    flakes = {}
    passes = {}
    for (test, result) in tests.iteritems():
        if include_expected or result.get('is_unexpected'):
            actual_results = result['actual'].split()
            expected_results = result['expected'].split()
            if len(actual_results) > 1:
                if actual_results[1] in expected_results:
                    flakes[test] = actual_results[0]
                else:
                    # We report the first failure type back, even if the second
                    # was more severe.
                    failures[test] = actual_results[0]
            elif actual_results[0] == 'PASS':
                passes[test] = result
            else:
                failures[test] = actual_results[0]

    return (passes, failures, flakes)


def convert_trie_to_flat_paths(trie, prefix=None):
    # Cloned from webkitpy.layout_tests.layout_package.json_results_generator
    # so that this code can stand alone.
    result = {}
    for name, data in trie.iteritems():
        if prefix:
            name = prefix + "/" + name

        if len(data) and not "actual" in data and not "expected" in data:
            result.update(convert_trie_to_flat_paths(data, name))
        else:
            result[name] = data

    return result


class LayoutTestsSplitter(object):
  def handles_step(self, step):
    return step['name'] == 'webkit_tests'

  def split_step(self, step, build, builder_name, master_url):
    # WTF?  The android bots call it archive_webkit_results and the rest call it archive_webkit_tests_results?
    archive_names = ['archive_webkit_results', 'archive_webkit_tests_results']
    archive_step = next((step for step in build['steps'] if step['name'] in archive_names), None)
    if not archive_step:
      print json.dumps(build['steps'], indent=1)
      log.warn("Failed to find archive step for build %s" % build['number'])
      return None

    html_results_url = archive_step['urls'].get('layout test results')
    # FIXME: Here again, Android is a special snowflake.
    if not html_results_url:
      html_results_url = archive_step['urls'].get('results')

    if not html_results_url:
      log.warn("Failed to find html results url for archive step in build %s" % build['number'])
      print json.dumps(archive_step, indent=1)
      return None

    # !@?#!$^&$% WTF HOW DO URLS HAVE \r in them!?!
    html_results_url = html_results_url.replace('\r', '')

    jsonp_url = urlparse.urljoin(html_results_url, 'failing_results.json')
    # FIXME: Silly that this is still JSONP.
    jsonp_string = requests.get(jsonp_url).text
    json_string = jsonp_string[len('ADD_RESULTS('):-len(');')]
    try:
      results = json.loads(json_string)
      passes, failures, flakes = decode_results(results)
      if failures:
        return failures
    except ValueError:
      print archive_step['urls']
      print html_results_url
      print "Failed %s, at decode of: %s" % (jsonp_url, jsonp_string)

    # Failed to split, just group with the general failures.
    return None


STEP_SPLITTERS = [
  LayoutTestsSplitter(),
  GTestSplitter(),
]
