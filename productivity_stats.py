#!/usr/bin/env python

import argparse
import datetime
import glob
import fileinput
import itertools
import numpy
import operator
import re
import requests
import requests_cache
import subprocess
import sys
import os

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
# Record first CQ time.
# Blink Rolls
# Reverts


CACHE_LOCATION = 'productivity_stats_cache'

# Default date format when stringifying python dates.
PYTHON_DATE_FORMAT_MS = "%Y-%m-%d %H:%M:%S.%f"
# Without milliseconds:
PYTHON_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


CSV_FIELD_ORDER = [
    'repository',
    'commit_id',
    'svn_revision',
    'commit_author',
    'review_id',
    'branch',
    'review_create_date',
    'review_sent_date',
    'first_lgtm_date',
    'first_cq_start_date',
    'commit_date',
    'branch_release_date',
]

# These are specific to Chrome and would need to be updated for Blink, etc.
RIETVELD_URL = "https://codereview.chromium.org"

# Times reported here are GMT, release-went-live times.
RELEASE_HISTORY_CSV_URL = 'http://omahaproxy.appspot.com/history'

# Authors which are expected to not have a review url.
IGNORED_AUTHORS = [
    'chrome-admin@google.com',
    'chrome-release@google.com',
    'chromeos-lkgm@google.com',
]

REPOSITORIES = [
    {
        'name': 'chrome',
        'relative_path': '.',
        'svn_url': 'svn://svn.chromium.org/chrome/trunk/src',
        'branch_heads': 'refs/remotes/branch-heads'
    },
    {
        'name': 'blink',
        'relative_path': 'third_party/WebKit',
        'svn_url': 'svn://svn.chromium.org/blink/trunk',
        'branch_heads': 'refs/remotes/branch-heads/chromium'
    }
]

# For matching git commit messages:
# FIXME: This may need to be repository-relative for Skia, etc.
REVIEW_REGEXP = re.compile(r"Review URL: %s/(?P<review_id>\d+)" % RIETVELD_URL)


def fetch_recent_branches(repository):
    args = [
        'git', 'for-each-ref',
        '--sort=-committerdate',
        '--format=%(refname)',
        repository['branch_heads']
    ]
    for_each_ref_text = subprocess.check_output(args, cwd=repository['relative_path'])
    branch_paths = for_each_ref_text.strip('\n').split('\n')
    branch_names = map(lambda name: name.split('/')[-1], branch_paths)
    # Only bother looking at base branches (ignore 1234_1, etc.)
    branch_names = filter(lambda name: re.match('^\d+$', name), branch_names)
    # Even though the branches are sorted in commit time, we're still
    # going to sort them in integer order for our purposes.
    # Ordered from oldest to newest.
    return sorted(branch_names, key=int, reverse=True)


def path_for_branch(repository, name):
    return "%s/%s" % (repository['branch_heads'], name)


def parse_datetime_ms(date_string):
    return datetime.datetime.strptime(date_string, PYTHON_DATE_FORMAT_MS)


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
        date = parse_datetime_ms(date_string)
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


def svn_revision_from_lines(lines, repository):
    revision_regexp = re.compile(r"git-svn-id: %s@(?P<svn_revision>\d+) \w+" % repository['svn_url'])
    for line in lines:
        match = revision_regexp.match(line)
        if match:
            return int(match.group('svn_revision'))


def fetch_review(review_id):
    review_url = "%s/api/%s?messages=true" % (RIETVELD_URL, review_id)
    try:
        return requests.get(review_url).json()
    except ValueError:
        log.error("Error parsing: %s" % review_url)


def first_lgtm_date(review):
    for message in review['messages']:
        if message['approval']:
            return parse_datetime_ms(message['date'])


def first_cq_start_date(review):
    for message in review['messages']:
        # We could also look for "The CQ bit was checked by" instead
        # but I'm not sure how long rietveld has been adding that.
        if message['text'].startswith("CQ is trying da patch."):
            return parse_datetime_ms(message['date'])


def change_times(branch_names, branch_release_times, commit_id, branch, repository):
    change = {
        'repository': repository['name'],
        'commit_id': commit_id,
        'branch': branch,
    }

    args = ['git', 'log', '-1', '--pretty=format:%ct%n%cn%n%b', commit_id]
    log_text = subprocess.check_output(args, cwd=repository['relative_path'])

    lines = log_text.split("\n")
    change['commit_date'] = datetime.datetime.utcfromtimestamp(int(lines.pop(0)))
    change['commit_author'] = lines.pop(0)
    change['branch_release_date'] = branch_release_times[branch]

    change['review_id'] = review_id_from_lines(lines)
    if not change['review_id']:
        if change['commit_author'] not in IGNORED_AUTHORS:
            log.debug("Skipping %s from %s no Review URL" %
                (commit_id, change['commit_author']))
        return None

    review = fetch_review(change['review_id'])
    if not review:
        log.debug("Skipping %s, failed to fetch/parse review JSON." % commit_id)
        return None
    change['review_create_date'] = parse_datetime_ms(review["created"])
    change['review_sent_date'] = parse_datetime_ms(review["messages"][0]['date'])
    change['first_lgtm_date'] = first_lgtm_date(review)
    change['first_cq_start_date'] = first_cq_start_date(review)
    change['svn_revision'] = svn_revision_from_lines(lines, repository)

    return change


def _convert_key(change, key):
    value = change[key]
    if value and key.endswith('_date'):
        return value.strftime(PYTHON_DATE_FORMAT)
    return str(value)


def _convert_dates(field_value_tuple):
    field, value = field_value_tuple
    if value == 'None':
        return field, None
    if field.endswith('_date'):
        value = datetime.datetime.strptime(value, PYTHON_DATE_FORMAT)
    return field, value


def csv_line(change, fields):
    return ",".join(map(lambda field: _convert_key(change, field), fields))


def merge_base(repository_path, commit_one, commit_two=None):
    if commit_two is None:
        commit_two = 'origin/master'
    args = ['git', 'merge-base', commit_one, commit_two]
    return subprocess.check_output(args, cwd=repository_path).strip('\n')


def commits_new_in_branch(branch, previous_branch, repository):
    repository_path = repository['relative_path']
    base_new = merge_base(repository_path, path_for_branch(repository, branch))
    base_old = merge_base(repository_path, path_for_branch(repository, previous_branch))
    args = [
        'git', 'rev-list',
        '--abbrev-commit', '%s..%s' % (base_old, base_new)
    ]
    rev_list_output = subprocess.check_output(args, cwd=repository_path)
    stripped_output = rev_list_output.strip('\n')
    # "".split("\n") returns [''] which will confuse callers.
    if not stripped_output:
        return []
    return stripped_output.split('\n')


def check_for_stale_checkout(repository, branch_names, branch_release_times):
    repository_path = repository['relative_path']
    released_branches = branch_release_times.keys()
    latest_released_branch = sorted(released_branches, key=int, reverse=True)[0]
    latest_local_branch = branch_names[0]
    if int(latest_local_branch) < int(latest_released_branch):
        log.fatal("latest local branch %s is older than latest released branch %s in \"%s\" (%s); run git fetch to update." %
            (latest_local_branch, latest_released_branch,
                repository['relative_path'], repository['name']))
        subprocess.check_call(['git', 'fetch'], cwd=repository_path)


def csv_path(branch, repository):
    return os.path.join(CACHE_LOCATION, "%s_%s.csv" % (branch, repository['name']))


def validate_checkouts_and_fetch_branch_names(branch_release_times):
    branch_names = None
    for repository in REPOSITORIES:
        recent_branches = fetch_recent_branches(repository)
        # Filter out any non-released branches (failed to build, etc.)
        # According to Laforge, Canaries fail to release for 3 reasons:
        # 1. Official Build/Compile is broken.
        # 2. Signing failed.
        # 3. Insufficient builds to bother (weekends, holidays)
        # Right now we don't track a separate time-diff/reason for non-released builds, but should.
        filtered_branches = filter(lambda name: name in branch_release_times, recent_branches)

        check_for_stale_checkout(repository, recent_branches, branch_release_times)

        # Save the Chrome branches for use by the rest of the function.
        if repository['name'] == 'chrome':
            branch_names = recent_branches
    return branch_names


def fetch_command(args):
    branch_release_times = fetch_branch_release_times()
    branch_names = validate_checkouts_and_fetch_branch_names(branch_release_times)

    branch_limit = min(len(branch_names) - 1, args.branch_limit)

    if not os.path.exists(CACHE_LOCATION):
        os.makedirs(CACHE_LOCATION)

    for index in range(branch_limit):
        branch, previous_branch = branch_names[index], branch_names[index + 1]
        for repository in REPOSITORIES:
            with open(csv_path(branch, repository), "w") as csv_file:
                csv_file.write(",".join(CSV_FIELD_ORDER) + "\n")
                commits = commits_new_in_branch(branch, previous_branch, repository)
                log.info("%s commits between branch %s and %s in %s" %
                    (len(commits), branch, previous_branch, repository['name']))
                for commit_id in commits:
                    change = change_times(branch_names, branch_release_times,
                        commit_id, branch, repository)
                    if change:
                        csv_file.write(csv_line(change, CSV_FIELD_ORDER) + "\n")
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

    return [dict(map(_convert_dates, zip(CSV_FIELD_ORDER, split_csv_line(line)))) for line in csv_file]


def seconds_between_keys(change, earlier_key, later_key):
    return int((change[later_key] - change[earlier_key]).total_seconds())


def print_stats(changes):
    def total_seconds(change):
        return seconds_between_keys(change, 'review_create_date', 'branch_release_date')
    times = map(total_seconds, changes)
    print "Records: ", len(times)
    print "Mean:", datetime.timedelta(seconds=numpy.mean(times))
    print "Precentiles:"
    for percentile in (1, 10, 25, 50, 75, 90, 99):
        seconds = numpy.percentile(times, percentile)
        time_delta = datetime.timedelta(seconds=seconds)
        print "%s%%: %s" % (percentile, time_delta)


def load_changes():
    changes = []
    for path in glob.iglob(os.path.join(CACHE_LOCATION, '*.csv')):
        records = read_csv(path)
        log.debug("%s changes in %s" % (len(records), path))
        changes.extend(records)
    return changes


def stats_command(args):
    changes = load_changes()
    changes.sort(key=operator.itemgetter('repository', 'svn_revision'))
    for repository, per_repo_changes in itertools.groupby(changes, key=operator.itemgetter('repository')):
        print "\nRepository: %s" % repository
        print_stats(per_repo_changes)


# FIXME: There must be a simpler way to write this.
def change_stats(change, ordered_events):
    results = {}
    reversed_names = list(reversed(ordered_events))
    for index, event_name in enumerate(reversed_names):
        date = change[event_name]
        if not date:
            results[event_name] = 0
            continue

        previous_index = index + 1
        previous_date = None
        while previous_index < len(reversed_names) and not previous_date:
            previous_name = reversed_names[previous_index]
            previous_date = change[previous_name]
            previous_index += 1

        if not previous_date:
            continue

        seconds = (date - previous_date).total_seconds()
        if seconds < 0:
            # FIXME: This isn't quite right, we should skip these entries entirely.
            log.debug("Time between %s and %s in %s is negative (%s), ignoring." % (event_name, reversed_names[previous_index], change['commit_id'], seconds))
            seconds = 0
        results[event_name] = seconds
    return results


def graph_command(args):
    ordered_events = [
        'review_create_date',
        'review_sent_date',
        'first_lgtm_date',
        'first_cq_start_date',
        'commit_date',
        'branch_release_date',
    ]

    changes = load_changes()
    changes.sort(key=operator.itemgetter('repository', 'svn_revision'))
    for repository, per_repo_changes in itertools.groupby(changes, key=operator.itemgetter('repository')):
        print "\nRepository: %s" % repository
        print ordered_events[1:]
        for change in per_repo_changes:
            stats = change_stats(change, ordered_events)
            print map(lambda name: stats[name], ordered_events[1:]), " // %s" % change['commit_id']


def main(args):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    parser.add_argument('chrome_path')
    fetch_parser = subparsers.add_parser('fetch')
    fetch_parser.add_argument('--branch-limit', default=7)
    fetch_parser.set_defaults(func=fetch_command)
    stats_parser = subparsers.add_parser('stats')
    stats_parser.set_defaults(func=stats_command)
    graph_parser = subparsers.add_parser('graph')
    graph_parser.set_defaults(func=graph_command)
    args = parser.parse_args(args)

    # This script assume's its being run from the root of a chrome checkout
    # we could remove this restriction by fixing uses of the REPOSITORIES
    # relative_path key.
    os.chdir(args.chrome_path)
    return args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
