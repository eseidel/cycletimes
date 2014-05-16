#!/usr/bin/env python

import subprocess
import requests
from datetime import datetime, timedelta
import re

import requests_cache
# CAREFUL: This caches everything, including omaha proxy lookups!
requests_cache.install_cache('productivity_stats')

# TODO:
# Verify that timezones are correct for all timestamps!
# (Timezones can mean hours, which is a lot of time!)
# Spit out real CSV (for later processing)
# Record first LGTM time
# Record first CQ time.
#

# def commit_date(commitish):
#   args = ['git', 'log', '-1', "--pretty=format:%ct", commitish]
#   return datetime.fromtimestamp(int(subprocess.check_output(args)))

# class Branch(object):
#   def __init__(self, branch_path):
#       self.path = branch_path
#       self.date = commit_date(branch_path)


# args = ['git', 'show-ref']
# branch_text = subprocess.check_output(args)
# all_ref_paths = [line.split(' ')[1] for line in branch_text.split("\n") if line]
# all_branch_paths = filter(lambda name: name.startswith("refs/remotes/branch-heads"), all_ref_paths)
# all_branches = map(Branch, all_branch_paths)

# Default date format when stringifying python dates.
PYTHON_DATE_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
# Currently only bothering looking at the 50 most recent branches.
BRANCH_LIMIT = 50
# may require gclient sync --with_branch_heads && git fetch
BRANCH_HEADS_PATH = 'refs/remotes/branch-heads'

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


def fetch_branch_release_times():
    release_times = {}
    history_text = requests.get("http://omahaproxy.appspot.com/history").text
    for line in history_text.strip('\n').split('\n'):
        os, channel, version, date_string = line.split(',')
        date = datetime.strptime(date_string, PYTHON_DATE_FORMAT)
        branch = version.split('.')[2]
        last_date = release_times.get(branch)
        if not last_date or last_date > date:
            release_times[branch] = date
    return release_times

branch_release_times = fetch_branch_release_times()
branch_names = fetch_recent_branches(BRANCH_LIMIT, branch_release_times)


def contains_commit(branch_id, commit_id):
    # These are supposed to work but didn't:
    #git log <ref>..<sha1>'
    #args = ['git', 'rev-list', '-n', '1', "%s...%s" % (branch_id, commit_id)]
    args = ['git', 'merge-base', '--is-ancestor', commit_id, branch_id]
    return subprocess.call(args) == 0


def oldest_released_branch(commit_id, expected_branch=None):
    # Fast-path for repeated lookups of the same branch (like when walking back through git history)
    if expected_branch and contains_commit(path_for_branch(expected_branch), commit_id):
        expected_index = branch_names.index(expected_branch)
        if expected_index == 0:
            return expected_branch
        before_expected_branch = branch_names[expected_index - 1]
        if not contains_commit(path_for_branch(before_expected_branch), commit_id):
            return expected_branch

    for branch in branch_names:
        if contains_commit(path_for_branch(branch), commit_id):
            return branch
    return None


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
    issue_created_text = requests.get(review_url).json()["created"]
    return datetime.strptime(issue_created_text, PYTHON_DATE_FORMAT)


# FIXME: This should be a generator which returns objects.
commit_id = "b9e6bdf4ae49e553d08bbea5efb3a355a8484987"
commit_separator = "==============================="
args = [
    'git', 'log',
    '-n', "1000",
    '--pretty=format:%h%n%ct%n%b%n' + commit_separator,
    commit_id
]
log_text = subprocess.check_output(args)

times = []
last_branch = None
for message in log_text.split(commit_separator + "\n"):
    # The last message is often empty.
    if not message:
        break
    lines = message.split("\n")
    commit_id = lines.pop(0)
    commit_date = datetime.fromtimestamp(int(lines.pop(0)))
    review_id = review_id_from_lines(lines)
    if not review_id:
        print "Skipping %s, no Review URL" % commit_id
        continue

    issue_created = creation_date_for_review(review_id)
    svn_revision = svn_revision_from_lines(lines)
    branch = oldest_released_branch(commit_id, last_branch)
    if branch:
        last_branch = branch
    branch_release_date = branch_release_times.get(branch)
    if not branch_release_date:
        print "ERROR: No release date for %s???" % branch
        continue

    delta_to_review = commit_date - issue_created
    delta_to_release = branch_release_date
    total_delta = branch_release_date - issue_created
    print commit_id, "r%s" % svn_revision, review_id, branch, "%24s" % total_delta
    times.append(total_delta.total_seconds())

avg_seconds = sum(times) / len(times)
print "Average:", timedelta(seconds=avg_seconds)
