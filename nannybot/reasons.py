# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json
import logging
import requests
import sys
import urllib
import urlparse
import argparse
import re

# This is relative to build/scripts:
# https://chromium.googlesource.com/chromium/tools/build/+/master/scripts
BUILD_SCRIPTS_PATH = "/src/build/scripts"
sys.path.append(BUILD_SCRIPTS_PATH)
from common import gtest_utils

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


def build_url(master_url, builder_name, build_number):
  quoted_name = urllib.pathname2url(builder_name)
  args = (master_url, quoted_name, build_number)
  return "%s/builders/%s/builds/%s" % args


def stdio_for_step(master_url, builder_name, build, step):
# FIXME: Should get this from the step in some way?
  base_url = build_url(master_url, builder_name, build['number'])
  stdio_url = "%s/steps/%s/logs/stdio/text" % (base_url, step['name'])

  log.debug("Fetching: %s" % stdio_url)
  try:
    return requests.get(stdio_url).text
  except requests.exceptions.ConnectionError, e:
    # Some builders don't save logs for whatever reason.
    log.error('Failed to fetch %s: %s' % (stdio_url, e))
    return None


# These are reason finders, more than splitters?
class GTestSplitter(object):
  def handles_step(self, step):
    step_name = step['name']
    # Silly heuristic, at least we won't bother processing
    # stdio from gclient revert, etc.
    if step_name.endswith('tests'):
      return True

    KNOWN_STEPS = [
      # There are probably other gtest steps not named 'tests'.
    ]
    return step_name in KNOWN_STEPS

  def split_step(self, step, build, builder_name, master_url):
    stdio_log = stdio_for_step(master_url, builder_name, build, step)
    # Can't split if we can't get the logs.
    if not stdio_log:
      return None

    # Lines this fails for:
    #[  FAILED  ] ExtensionApiTest.TabUpdate, where TypeParam =  and GetParam() =  (10907 ms)

    log_parser = gtest_utils.GTestLogParser()
    for line in stdio_log.split('\n'):
      log_parser.ProcessLine(line)

    failed_tests = log_parser.FailedTests()
    log.debug('Found %s failed tests.' % len(failed_tests))

    if failed_tests:
      return failed_tests
    # Failed to split, just group with the general failures.
    log.debug('First Line: %s' % stdio_log.split('\n')[0])
    return None


# Our Android tests produce very gtest-like output, but not
# quite GTestLogParser-compatible (it parse the name of the
# test as org.chromium).

class JUnitSplitter(object):
  def handles_step(self, step):
    KNOWN_STEPS = [
      'androidwebview_instrumentation_tests',
      'mojotest_instrumentation_tests', # Are these always java?
    ]
    return step['name'] in KNOWN_STEPS

  FAILED_REGEXP = re.compile('\[\s+FAILED\s+\] (?P<test_name>\S+)( \(.*\))?$')

  def failed_tests_from_stdio(self, stdio):
    failed_tests = []
    for line in stdio.split('\n'):
      match = self.FAILED_REGEXP.search(line)
      if match:
        failed_tests.append(match.group('test_name'))
    return failed_tests

  def split_step(self, step, build, builder_name, master_url):
    stdio_log = stdio_for_step(master_url, builder_name, build, step)
    # Can't split if we can't get the logs.
    if not stdio_log:
      return None

    failed_tests = self.failed_tests_from_stdio(stdio_log)
    log.debug('Found %s failed tests.' % len(failed_tests))

    if failed_tests:
      return failed_tests
    # Failed to split, just group with the general failures.
    log.debug('First Line: %s' % stdio_log.split('\n')[0])
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
    url_to_build = build_url(master_url, builder_name, build['number'])

    if not archive_step:
      log.warn('No archive step in %s' % url_to_build)
      print json.dumps(build['steps'], indent=1)
      return None

    html_results_url = archive_step['urls'].get('layout test results')
    # FIXME: Here again, Android is a special snowflake.
    if not html_results_url:
      html_results_url = archive_step['urls'].get('results')

    if not html_results_url:
      log.warn('No results url for archive step in %s' % url_to_build)
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


class CompileSplitter(object):
  def handles_step(self, step):
    return step['name'] == 'compile'

# Compile example:
# FAILED: /mnt/data/b/build/goma/gomacc ...
# ../../v8/src/base/platform/time.cc:590:7: error: use of undeclared identifier 'close'

# Linker example:
# FAILED: /b/build/goma/gomacc ...
# obj/chrome/browser/extensions/interactive_ui_tests.extension_commands_global_registry_apitest.o:extension_commands_global_registry_apitest.cc:function extensions::SendNativeKeyEventToXDisplay(ui::KeyboardCode, bool, bool, bool): error: undefined reference to 'gfx::GetXDisplay()'

  def split_step(self, step, build, builder_name, master_url):
    stdio = stdio_for_step(master_url, builder_name, build, step)
    # Can't split if we can't get the logs.
    if not stdio:
      return None

    compile_regexp = re.compile(r'(?P<path>.*):(?P<line>\d+):(?P<column>\d+): error:')

    # FIXME: I'm sure there is a cleaner way to do this.
    next_line_is_failure = False
    for line in stdio.split('\n'):
      if not next_line_is_failure:
        if line.startswith('FAILED: '):
          next_line_is_failure = True
        continue

      match = compile_regexp.match(line)
      if match:
        return ['%s:%s' % (match.group('path'), match.group('line'))]
      break

    return None


# This is a hack I wrote because all the perf bots are failing with:
# E    0.009s Main  File not found /b/build/slave/Android_GN_Perf/build/src/out/step_results/dromaeo.jslibstyleprototype
# and it's nice to group them by something at least!
class GenericRunTests(object):
  def handles_step(self, step):
    return True

  def split_step(self, step, build, builder_name, master_url):
    stdio = stdio_for_step(master_url, builder_name, build, step)
    # Can't split if we can't get the logs.
    if not stdio:
      return None

    last_line = None
    for line in stdio.split('\n'):
      if last_line and line.startswith('exit code (as seen by runtest.py):'):
        return [last_line]
      last_line = line


STEP_SPLITTERS = [
  CompileSplitter(),
  LayoutTestsSplitter(),
  JUnitSplitter(),
  GTestSplitter(),
  GenericRunTests(),
]


# For testing:
def main(args):
  parser = argparse.ArgumentParser()
  parser.add_argument('stdio_url', action='store')
  args = parser.parse_args(args)

  # https://build.chromium.org/p/chromium.win/builders/XP%20Tests%20(1)/builds/31886/steps/browser_tests/logs/stdio
  url_regexp = re.compile('(?P<master_url>.*)/builders/(?P<builder_name>.*)/builds/(?P<build_number>.*)/steps/(?P<step_name>.*)/logs/stdio')
  match = url_regexp.match(args.stdio_url)
  if not match:
    print "Failed to parse URL: %s" % args.stdio_url
    sys.exit(1)

  step = {
    'name': match.group('step_name'),
  }
  build = {
    'number': match.group('build_number'),
  }
  splitter = next((splitter for splitter in STEP_SPLITTERS if splitter.handles_step(step)), None)
  builder_name = urllib.unquote_plus(match.group('builder_name'))
  master_url = match.group('master_url')
  print splitter.split_step(step, build, builder_name, master_url)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
