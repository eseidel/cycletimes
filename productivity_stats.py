#!/usr/bin/env python

import argparse
import datetime
import itertools
import numpy
import re
import requests
import requests_cache
import subprocess
import sys

# CAREFUL: This caches everything, including omaha proxy lookups!
requests_cache.install_cache('productivity_stats')


import logging

# Python logging is stupidly verbose to configure.
def setup_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(levelname)s: %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger


log = setup_logging()

# TODO:
# Verify that timezones are correct for all timestamps!
# (Timezones can mean hours, which is a lot of time!)
# Spit out real CSV (for later processing)
# Record first LGTM time
# Record first CQ time.
# Blink Rolls
# Reverts
# Re-write to walk down releases instead of commits.

# Default date format when stringifying python dates.
PYTHON_DATE_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
# Currently only bothering looking at the 50 most recent branches.
BRANCH_LIMIT = 100
# may require gclient sync --with_branch_heads && git fetch
BRANCH_HEADS_PATH = 'refs/remotes/branch-heads'

CSV_FIELD_ORDER = ['commit_id', 'svn_revision', 'review_id', 'branch', 'issue_created_date', 'commit_date', 'branch_release_date']

# For matching git commit messages:
REVIEW_REGEXP = re.compile(r"Review URL: https://codereview\.chromium\.org/(?P<review_id>\d+)")
SVN_REVISION_REGEXP = re.compile(r"git-svn-id: svn://svn.chromium.org/chrome/trunk/src@(?P<svn_revision>\d+) \w+")


def fetch_recent_branches(limit=None, branch_release_times=None):
    args = [
        'git', 'for-each-ref',
        '--sort=-committerdate',
        '--format=%(refname)',
        BRANCH_HEADS_PATH
    ]
    if limit:
        args.append('--count=%s' % limit)
    branch_paths = subprocess.check_output(args).strip('\n').split('\n')
    branch_names = map(lambda name: name.split('/')[-1], branch_paths)
    # Only bother looking at base branches (ignore 1234_1, etc.)
    branch_names = filter(lambda name: re.match('^\d+$', name), branch_names)
    # Even though the branches are sorted in commit time, we're still
    # going to sort them in integer order for our purposes.
    # Ordered from oldest to newest.

    # Filter out any non-released branches.
    if branch_release_times:
        branch_names = filter(lambda name: name in branch_release_times, branch_names)

    return sorted(branch_names, key=int)


def path_for_branch(name):
    return "%s/%s" % (BRANCH_HEADS_PATH, name)


def parse_datetime(date_string):
    return datetime.datetime.strptime(date_string, PYTHON_DATE_FORMAT)


def fetch_branch_release_times():
    release_times = {}
    # Always grab the most recent release history.
    with requests_cache.disabled():
        history_text = requests.get("http://omahaproxy.appspot.com/history").text
    for line in history_text.strip('\n').split('\n'):
        os, channel, version, date_string = line.split(',')
        date = parse_datetime(date_string)
        branch = version.split('.')[2]
        last_date = release_times.get(branch)
        if not last_date or last_date > date:
            release_times[branch] = date
    return release_times


def review_id_from_lines(lines):
    for line in lines:
        match = REVIEW_REGEXP.match(line)
        if match:
            return int(match.group("review_id"))


def svn_revision_from_lines(lines):
    for line in lines:
        match = SVN_REVISION_REGEXP.match(line)
        if match:
            return int(match.group('svn_revision'))


def creation_date_for_review(review_id):
    review_url = "https://codereview.chromium.org/api/%s" % review_id
    try:
        return parse_datetime(requests.get(review_url).json()["created"])
    except ValueError:
        log.error("Error parsing: %s" % review_url)


def change_times(branch_names, branch_release_times, commit_id, branch):
    global last_branch
    change = {}

    args = ['git', 'log', '-1', '--pretty=format:%ct%n%b', commit_id]
    log_text = subprocess.check_output(args)

    lines = log_text.split("\n")
    change['commit_id'] = commit_id
    change['commit_date'] = datetime.datetime.fromtimestamp(int(lines.pop(0)))
    change['review_id'] = review_id_from_lines(lines)
    if not change['review_id']:
        log.debug("Skipping %s, no Review URL" % change['commit_id'])
        return None

    change['issue_created_date'] = creation_date_for_review(change['review_id'])
    if not change['issue_created_date']:
        log.debug("Skipping %s, failed to fetch/parse review JSON." % change['commit_id'])
        return None
    change['svn_revision'] = svn_revision_from_lines(lines)

    change['branch'] = branch
    change['branch_release_date'] = branch_release_times.get(change['branch'])
    # Branches are pre-filtered to include only released branches.
    if not change['branch_release_date']:
        log.error("No release date for %s???" % change['branch'])
        return None

    return change


def csv_line(change, fields):
    return ",".join(map(lambda field: str(change[field]), fields))


def merge_base(commit_one, commit_two=None):
    if commit_two is None:
        commit_two = 'origin/master'
    args = ['git', 'merge-base', commit_one, commit_two]
    return subprocess.check_output(args).strip('\n')


def commits_new_in_branch(branch, previous_branch):
    base_new = merge_base(path_for_branch(branch))
    base_old = merge_base(path_for_branch(previous_branch))
    args = ['git', 'rev-list', '--abbrev-commit', '%s..%s' % (base_old, base_new)]
    return subprocess.check_output(args).strip('\n').split('\n')


# http://stackoverflow.com/questions/6822725/rolling-or-sliding-window-iterator-in-python
def window(seq, n=2):
    "Returns a sliding window (of width n) over data from the iterable"
    "   s -> (s0,s1,...s[n-1]), (s1,s2,...,sn), ...                   "
    it = iter(seq)
    result = tuple(itertools.islice(it, n))
    if len(result) == n:
        yield result
    for elem in it:
        result = result[1:] + (elem,)
        yield result

def check_for_stale_checkout(branch_names, branch_release_times):
    latest_json_branch = int(sorted(branch_release_times.keys(), key=int, reverse=True)[0])
    latest_local_branch = next(reversed(branch_names))
    if latest_local_branch < latest_json_branch:
        log.warn("latest local branch %s is older than latest released branch %s, run git fetch.", latest_local_branch, latest_json_branch)


def fetch_command(args):
    branch_release_times = fetch_branch_release_times()
    branch_names = fetch_recent_branches(BRANCH_LIMIT, branch_release_times)
    check_for_stale_checkout(branch_names, branch_release_times)
    line_target = 10000

    change_count = 0
    skipped = 0
    print ",".join(CSV_FIELD_ORDER)
    for branch, previous_branch in window(reversed(branch_names)):
        commits = commits_new_in_branch(branch, previous_branch)
        log.info("%s commits between branch %s and %s" % (len(commits), branch, previous_branch))
        for commit_id in commits:
            change = change_times(branch_names, branch_release_times, commit_id, branch)
            if change:
                print csv_line(change, CSV_FIELD_ORDER)
                change_count += 1
            else:
                skipped += 1
        log.debug("%s of %s" % (change_count, line_target))
        if change_count >= line_target:
            break
    total = change_count + skipped
    log.info("Skipped %s out of %s (%d%%)" % (skipped, total, (float(skipped) / total) * 100))

def process_command(args):
    csv_file = open(args.csv_file)
    fields = csv_file.readline().strip('\n').split(',')
    if fields != CSV_FIELD_ORDER:
        log.error("CSV Field mismatch, got: %s, expected: %s" % (fields, CSV_FIELD_ORDER))
        return 1

    changes = [dict(zip(CSV_FIELD_ORDER, line.strip('\n').split(','))) for line in csv_file]
    times = map(lambda change: (parse_datetime(change['branch_release_date']) - parse_datetime(change['issue_created_date'])).total_seconds(), changes)
    print "Records: ", len(times)
    print "Mean:", datetime.timedelta(seconds=numpy.mean(times))
    print "Precentiles:"
    for percentile in (1, 10, 25, 50, 75, 90, 99):
        print "%s%%: %s" % (percentile, datetime.timedelta(seconds=numpy.percentile(times, percentile)))


def main(args):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    fetch_parser = subparsers.add_parser('fetch')
    fetch_parser.set_defaults(func=fetch_command)
    process_parser = subparsers.add_parser('process')
    process_parser.add_argument('csv_file')
    process_parser.set_defaults(func=process_command)
    args = parser.parse_args(args)
    return args.func(args)

if __name__ == "__main__":
    main(sys.argv[1:])
