# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import base64
import json
import requests
import string
import sys
import urlparse
import datetime


def master_name_from_url(master_url):
    return urlparse.urlparse(master_url).path.split('/')[-1]


def stats_url_for_master(master_name):
  # FIXME: May need to urlencode master_name.
  return ('https://chrome-infra-stats.appspot.com/'
      '_ah/api/stats/v1/stats/last/%s/'
      'overall__build__result__/2000' % master_name)


def printrow(row, widths, indent=0):
  if indent:
    print ' ' * indent,
  for index, cell in enumerate(row):
    print str(cell).rjust(widths[index]),
  print


def elapsed(seconds):
  return str(datetime.timedelta(seconds=round(seconds)))


def print_tree_stats(tree_name, stats_by_master):
  print
  print tree_name
  master_names = sorted(stats_by_master.keys())
  master_width = max(map(len, master_names))
  widths = (master_width, 10, 10, 10)
  printrow(('master', 'median', '99th', 'maximum'), widths, indent=1)
  for master_name in master_names:
    stats = stats_by_master[master_name]
    printrow((master_name,
        elapsed(stats['median']),
        elapsed(stats['ninetynine']),
        elapsed(stats['maximum'])), widths, indent=1)


def main(args):
  trees_url = ('https://chromium.googlesource.com/chromium/'
    'tools/build/+/master/scripts/slave/'
    'gatekeeper_trees.json?format=TEXT')
  trees_encoded = requests.get(trees_url).text
  trees = json.loads(base64.b64decode(trees_encoded))

  for tree_name, tree_config in trees.items():
    stats_by_master = {}
    for master_url in tree_config['masters']:
      master_name = master_name_from_url(master_url)
      url = stats_url_for_master(master_name)
      #print 'requesting %s...' % url
      stats_by_master[master_name] = requests.get(url).json()
    print_tree_stats(tree_name, stats_by_master)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))