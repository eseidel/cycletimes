# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest
import analysis
import json


class FailureAnalysisTest(unittest.TestCase):
    MERGE_REGRESSION_RANGES_JSON = """
[
    {
     "failing_revisions": {
      "v8": "22263",
      "chromium": "282006",
      "nacl": "13441",
      "blink": "177644"
     },
     "passing_revisions": {
      "v8": "22263",
      "chromium": "281980",
      "nacl": "13441",
      "blink": "177644"
     }
    },
    {
     "failing_revisions": {
      "v8": "22263",
      "chromium": "282022",
      "nacl": "13452",
      "blink": "177644"
     },
     "passing_revisions": {
      "v8": "22263",
      "chromium": "281989",
      "nacl": "13441",
      "blink": "177644"
     }
    }
]
"""

    def test_merge_regression_ranges(self):
        # This test could be much more complicated, right now
        # chromium is the only revision which differs.
        alerts = json.loads(self.MERGE_REGRESSION_RANGES_JSON)
        passing, failing = analysis.merge_regression_ranges(alerts)
        expected_pass = { 'v8': '22263', 'chromium': '281989', 'blink': '177644', 'nacl': '13441' }
        expected_fail = { 'v8': '22263', 'chromium': '282006', 'blink': '177644', 'nacl': '13441' }
        self.assertEquals(expected_fail, failing)
        self.assertEquals(expected_pass, passing)

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
   "failures": [
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
   "failures": [
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
