# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest
import analysis
import json


class FailureAnalysisTest(unittest.TestCase):
    # v8 stays the same, chromium differs, nacl is reduced.
    MERGE_REGRESSION_RANGES_JSON = """
[
    {
     "failing_revisions": {
      "v8": "22263",
      "chromium": "282006",
      "nacl": "13441"
     },
     "passing_revisions": {
      "v8": "22263",
      "chromium": "281980",
      "nacl": "13441"
     }
    },
    {
     "failing_revisions": {
      "v8": "22263",
      "chromium": "282022",
      "nacl": "13452"
     },
     "passing_revisions": {
      "v8": "22263",
      "chromium": "281989",
      "nacl": "13441"
     }
    }
]
"""

    MERGE_REGRESSION_RANGES_JSON_NULL = """
[
    {
     "failing_revisions": {
      "v8": "22263",
      "nacl": "13441"
     },
     "passing_revisions": null
    },
    {
     "failing_revisions": {
      "v8": "22263",
      "nacl": "13452"
     },
     "passing_revisions": {
      "v8": "22263",
      "nacl": "13441"
     }
    }
]
"""

    def test_merge_regression_ranges(self):
        alerts = json.loads(self.MERGE_REGRESSION_RANGES_JSON)
        passing, failing = analysis.merge_regression_ranges(alerts)
        expected_pass = { 'v8': '22263', 'chromium': '281989', 'nacl': '13441' }
        expected_fail = { 'v8': '22263', 'chromium': '282006', 'nacl': '13441' }
        self.assertEquals(expected_fail, failing)
        self.assertEquals(expected_pass, passing)

        alerts = json.loads(self.MERGE_REGRESSION_RANGES_JSON_NULL)
        passing, failing = analysis.merge_regression_ranges(alerts)
        expected_pass = None
        expected_fail = { 'v8': '22263', 'nacl': '13441' }
        self.assertEquals(expected_fail, failing)
        self.assertEquals(expected_pass, passing)


    def test_flatten_to_commit_list(self):
        passing = { 'v8': '1', 'chromium': '4'}
        failing = { 'v8': '2', 'chromium': '4'}
        commit_list = analysis.flatten_to_commit_list(passing, failing)
        self.assertEquals(commit_list, ['v8:2'])

        commit_list = analysis.flatten_to_commit_list(None, failing)
        self.assertEquals(commit_list, ['<=v8:2', '<=chromium:4'])

    MERGE_BY_RANGE_JSON = """
[
  {
   "likely_revisions": [
    "nacl:13442",
    "nacl:13452",
    "chromium:281990",
    "chromium:282022"
   ],
   "sort_key": "dromaeo.domcoreattr",
   "failure_keys": [
   ]
  },
  {
   "likely_revisions": [
    "nacl:13442",
    "nacl:13452",
    "chromium:282022",
    "chromium:281990"
   ],
   "sort_key": "dromaeo.jslibmodifyprototype",
   "failure_keys": [
   ]
  }
]
"""

    def test_merge_by_range(self):
        groups = json.loads(self.MERGE_BY_RANGE_JSON)
        merged = analysis.merge_by_range(groups)
        self.assertEquals(len(merged), 1)
        self.assertEquals(merged[0]['sort_key'], 'dromaeo.')


if __name__ == '__main__':
    unittest.main()
