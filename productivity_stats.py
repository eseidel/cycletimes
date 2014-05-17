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
# Record first LGTM time
# Record first CQ time.
# Blink Rolls
# Reverts
# Keep per-branch files in a directory.


# Default date format when stringifying python dates.
PYTHON_DATE_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
# may require gclient sync --with_branch_heads && git fetch
BRANCH_HEADS_PATH = 'refs/remotes/branch-heads'

CSV_FIELD_ORDER = [
    'repository',
    'commit_id',
    'svn_revision',
    'commit_author',
    'review_id',
    'branch',
    'review_create_date',
    'commit_date',
    'branch_release_date',
]

# These are specific to Chrome and would need to be updated for Blink, etc.
RIETVELD_URL = "https://codereview.chromium.org"
SVN_URL = "svn://svn.chromium.org/chrome/trunk/src"
RELEASE_HISTORY_CSV_URL = 'http://omahaproxy.appspot.com/history'

# Authors which are expected to not have a review url.
IGNORED_AUTHORS = [
    'chrome-admin@google.com',
    'chrome-release@google.com',
    'chromeos-lkgm@google.com',
]

# FIXME: This is a hack.
CHECKOUT_PATHS = {
    'chrome': '/src/chromium/src',
    'blink': '/src/chromium/src/third_party/WebKit',
}

# For matching git commit messages:
REVIEW_REGEXP = re.compile(r"Review URL: %s/(?P<review_id>\d+)" % RIETVELD_URL)
SVN_REVISION_REGEXP = re.compile(r"git-svn-id: %s@(?P<svn_revision>\d+) \w+" % SVN_URL)


def fetch_recent_branches():
    args = [
        'git', 'for-each-ref',
        '--sort=-committerdate',
        '--format=%(refname)',
        BRANCH_HEADS_PATH
    ]
    branch_paths = subprocess.check_output(args).strip('\n').split('\n')
    branch_names = map(lambda name: name.split('/')[-1], branch_paths)
    # Only bother looking at base branches (ignore 1234_1, etc.)
    branch_names = filter(lambda name: re.match('^\d+$', name), branch_names)
    # Even though the branches are sorted in commit time, we're still
    # going to sort them in integer order for our purposes.
    # Ordered from oldest to newest.
    return sorted(branch_names, key=int, reverse=True)


def path_for_branch(name):
    return "%s/%s" % (BRANCH_HEADS_PATH, name)


def parse_datetime(date_string):
    return datetime.datetime.strptime(date_string, PYTHON_DATE_FORMAT)


# FIXME: This could share logic with other CSV reading functions.
def fetch_branch_release_times():
    release_times = {}
    # Always grab the most recent release history.
    with requests_cache.disabled():
        history_text = requests.get(RELEASE_HISTORY_CSV_URL).text
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
    review_url = "%s/api/%s" % (RIETVELD_URL, review_id)
    try:
        review_json = requests.get(review_url).json()
        return parse_datetime(review_json["created"])
    except ValueError:
        log.error("Error parsing: %s" % review_url)


def change_times(branch_names, branch_release_times, commit_id, branch):
    change = {
        'repository': 'chrome',
        'commit_id': commit_id,
        'branch': branch,
    }

    args = ['git', 'log', '-1', '--pretty=format:%ct%n%cn%n%b', commit_id]
    log_text = subprocess.check_output(args)

    lines = log_text.split("\n")
    change['commit_date'] = datetime.datetime.fromtimestamp(int(lines.pop(0)))
    change['commit_author'] = lines.pop(0)
    change['review_id'] = review_id_from_lines(lines)
    if not change['review_id']:
        if change['commit_author'] not in IGNORED_AUTHORS:
            log.debug("Skipping %s from %s no Review URL" %
                (commit_id, change['commit_author']))
        return None

    change['review_create_date'] = creation_date_for_review(change['review_id'])
    if not change['review_create_date']:
        log.debug("Skipping %s, failed to fetch/parse review JSON." % commit_id)
        return None
    change['svn_revision'] = svn_revision_from_lines(lines)

    change['branch_release_date'] = branch_release_times.get(branch)
    # Branches are pre-filtered to include only released branches.
    if not change['branch_release_date']:
        log.error("No release date for %s???" % branch)
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
    args = [
        'git', 'rev-list',
        '--abbrev-commit', '%s..%s' % (base_old, base_new)
    ]
    return subprocess.check_output(args).strip('\n').split('\n')


def check_for_stale_checkout(branch_names, branch_release_times):
    released_branches = branch_release_times.keys()
    latest_released_branch = sorted(released_branches, key=int, reverse=True)[0]
    latest_local_branch = branch_names[0]
    if int(latest_local_branch) < int(latest_released_branch):
        log.warn("latest local branch %s is older than latest released branch %s, run git fetch.", latest_local_branch, latest_released_branch)


def fetch_command(args):
    branch_release_times = fetch_branch_release_times()
    branch_names = fetch_recent_branches()
    # Filter out any non-released branches (failed to build, etc.)
    branch_names = filter(lambda name: name in branch_release_times, branch_names)

    check_for_stale_checkout(branch_names, branch_release_times)
    branch_limit = min(len(branch_names) - 1, args.branch_limit)

    print ",".join(CSV_FIELD_ORDER)
    for index in range(branch_limit):
        branch, previous_branch = branch_names[index], branch_names[index + 1]
        commits = commits_new_in_branch(branch, previous_branch)
        log.info("%s commits between branch %s and %s" %
            (len(commits), branch, previous_branch))
        for commit_id in commits:
            change = change_times(branch_names, branch_release_times, commit_id, branch)
            if change:
                print csv_line(change, CSV_FIELD_ORDER)
        log.debug("Completed %s of %s branches" % (index + 1, branch_limit))


def split_csv_line(csv_line):
    return csv_line.strip('\n').split(',')


def read_csv(file_path):
    csv_file = open(file_path)
    fields = split_csv_line(csv_file.readline())
    if fields != CSV_FIELD_ORDER:
        log.error("CSV Field mismatch, got: %s, expected: %s" %
            (fields, CSV_FIELD_ORDER))
        return 1

    return [dict(zip(CSV_FIELD_ORDER, split_csv_line(line))) for line in csv_file]


def print_stats(changes):
    def total_seconds(change):
        return (parse_datetime(change['branch_release_date']) -
            parse_datetime(change['review_create_date'])).total_seconds()
    times = map(total_seconds, changes)
    print "Records: ", len(times)
    print "Mean:", datetime.timedelta(seconds=numpy.mean(times))
    print "Precentiles:"
    for percentile in (1, 10, 25, 50, 75, 90, 99):
        seconds = numpy.percentile(times, percentile)
        time_delta = datetime.timedelta(seconds=seconds)
        print "%s%%: %s" % (percentile, time_delta)


def process_command(args):
    print_stats(read_csv(args.csv_file))


def main(args):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    fetch_parser = subparsers.add_parser('fetch')
    fetch_parser.add_argument('--branch-limit', default=5)
    fetch_parser.set_defaults(func=fetch_command)
    process_parser = subparsers.add_parser('process')
    process_parser.add_argument('csv_file')
    process_parser.set_defaults(func=process_command)
    args = parser.parse_args(args)
    return args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
