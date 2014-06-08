#!/usr/bin/env python

import subprocess
import datetime
import itertools
import collections
import numpy
import sys
import os


# FIXME: These could be shared with cycletimes.py
ROOT = '/src/chromium/src'
REPOSITORIES = [
    {
        'name': 'chrome',
        'relative_path': '.',
    },
    {
        'name': 'blink',
        'relative_path': 'third_party/WebKit',
    },
    {
        'name': 'skia',
        'relative_path': 'third_party/skia/src',
    },
    {
        'name': 'v8',
        'relative_path': 'v8',
    },
]

FIELD_ORDER = [
    'month',
    'commits',
    'contributors',
    'mean_commits_per',
    'median_commits_per',
    'ninetieth_commits_per',
]


def _tuple_from_line(line):
    try:
        date_string, author = line.split('###')
    except Exception, e:
        print line
        print e
        raise
    date = datetime.datetime.utcfromtimestamp(int(date_string))
    return (date, author)


def _tuples_for_respository(repository):
    args = [
        'git',
        'log',
        '--pretty=format:%ct###%an',
    ]
    directory = os.path.join(ROOT, repository['relative_path'])
    log_text = subprocess.check_output(args, cwd=directory)
    return [_tuple_from_line(line) for line in log_text.split('\n')]


def _stats_from_tuples(month, tuples):
    stats = {}
    counter = collections.Counter(map(lambda data_and_author: data_and_author[1], tuples))
    stats['month'] = tuples[0][0].strftime('%m/%Y')
    stats['commits'] = len(tuples)
    stats['contributors'] = len(counter)
    stats['mean_commits_per'] = round(numpy.mean(counter.values()), 1)
    stats['median_commits_per'] = numpy.median(counter.values())
    stats['ninetieth_commits_per'] = numpy.percentile(counter.values(), 90)
    return stats


def main(args):
    for repository in REPOSITORIES:
        print 
        print repository['name']
        tuples = _tuples_for_respository(repository)
        print ' '.join(['month   ', 'com', 'con', 'avg', 'med', '90%'])
        for month, values in itertools.groupby(tuples, key=lambda date_and_author: date_and_author[0].month):
            stats = _stats_from_tuples(month, list(values))
            print ' '.join(map(str, map(lambda name: stats[name], FIELD_ORDER)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
