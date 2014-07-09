# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest
import reasons


class JUnitSplitterTest(unittest.TestCase):
    def test_failed_tests_from_stdio(self):
        splitter = reasons.JUnitSplitter()
        stdio = """
# C  367.973s Main  [  FAILED  ] org.chromium.android_webview.test.AwContentsClientShouldOverrideUrlLoadingTest#testCalledForUnsupportedSchemes
# C   59.798s Main  [  FAILED  ] org.chromium.mojo.system.impl.CoreImplTest#testAsyncWaiterWaitingOnDefaultInvalidHandle (CRASHED)
"""
        expected = [
            'org.chromium.android_webview.test.AwContentsClientShouldOverrideUrlLoadingTest#testCalledForUnsupportedSchemes',
            'org.chromium.mojo.system.impl.CoreImplTest#testAsyncWaiterWaitingOnDefaultInvalidHandle',
        ]
        self.assertEquals(splitter.failed_tests_from_stdio(stdio), expected)


if __name__ == '__main__':
    unittest.main()
