# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest
import analysis
import json


class FailureAnalysisTest(unittest.TestCase):
    MERGE_ALERTS_JSON = """
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
        alerts = json.loads(self.MERGE_ALERTS_JSON)
        passing, failing = analysis.merge_regression_ranges(alerts)
        expected_pass = { 'v8': '22263', 'chromium': '281989', 'blink': '177644', 'nacl': '13441' }
        expected_fail = { 'v8': '22263', 'chromium': '282006', 'blink': '177644', 'nacl': '13441' }
        self.assertEquals(expected_fail, failing)
        self.assertEquals(expected_pass, passing)


if __name__ == '__main__':
    unittest.main()
