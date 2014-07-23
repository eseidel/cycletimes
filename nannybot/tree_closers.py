# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import base64
import buildbot
import collections
import gatekeeper_extras
import json
import os
import requests
import sys

# This is relative to build/scripts:
# https://chromium.googlesource.com/chromium/tools/build/+/master/scripts
BUILD_SCRIPTS_PATH = "/src/build/scripts"
sys.path.append(BUILD_SCRIPTS_PATH)
from slave import gatekeeper_ng_config


# FIXME: Pull from:
# https://chromium.googlesource.com/chromium/tools/build/+/master/scripts/slave/gatekeeper.json?format=TEXT
CONFIG_PATH = os.path.join(BUILD_SCRIPTS_PATH, 'slave', 'gatekeeper.json')

TREES_JSON = "https://chromium.googlesource.com/chromium/tools/build/+/master/scripts/slave/gatekeeper_trees.json?format=TEXT"

def main(args):
    gatekeeper = gatekeeper_ng_config.load_gatekeeper_config(CONFIG_PATH)

    # WTF?  Why is Gitles format=text base64 encoding??
    trees_encoded = requests.get(TREES_JSON).text
    trees = json.loads(base64.b64decode(trees_encoded))

    tree_closers = collections.defaultdict(list)
    for tree_name, tree_config in trees.items():
        for master_url in tree_config['masters']:
            master_config = gatekeeper[master_url]
            if not master_config:
                print "ERROR: gatekeeper missing %s" % master_url
                continue

            master_name = buildbot.master_name_from_url(master_url)
            master_json = buildbot.fetch_master_json(master_url)
            builder_names = set(master_json['builders'].keys())
            builder_names -= gatekeeper_extras.excluded_builders(master_config)
            master_names = [master_name] * len(builder_names)
            tree_closers[tree_name].extend(zip(master_names, builder_names))

    # FIXME: This is not fully accurate.  It's possible for a non-excluded
    # builder to never be able to close the tree because none of its
    # steps can close the tree.  To do that we'd need to know what steps
    # are possible on a given builder which would mean fetching old builds
    # and taking a representive sample of step names (probably from last passing build).
    # Then we could use gatekeeper_extras.would_close_tree on each step name.

    for tree_name in sorted(tree_closers.keys()):
        print tree_name
        builder_tuples = sorted(tree_closers[tree_name])
        for master_name, builder_name in builder_tuples:
            print '  %s: %s' % (master_name, builder_name)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))