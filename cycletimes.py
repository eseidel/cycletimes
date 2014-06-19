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


import logging

# Python logging is stupidly verbose to configure.
def setup_logging():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger, handler


log, logging_handler = setup_logging()


CACHE_NAME = 'cycletimes_cache'
CACHE_FILE_REGEXP = re.compile(r'(?P<branch>\d+)_(?P<repository>\w+)\.csv')

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
    'lgtms',
    'first_lgtm_date',
    'last_lgtm_date',
    'cq_starts',
    'first_cq_start_date',
    'last_cq_start_date',
    'commit_date',
    'branch_release_date',
]

ALL_ORDERED_EVENTS = [
    'review_create_date',
    'review_sent_date',
    'first_lgtm_date',
    'last_lgtm_date',
    'first_cq_start_date',
    'last_cq_start_date',
    'commit_date',
    'branch_release_date',
]

# Only the events to show in the graph command.
GRAPH_ORDERED_EVENTS = [
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

# Bots which are expected to not have a review url:
NO_REVIEW_URL_AUTHORS = [
    'chrome-admin@google.com',
    'chrome-release@google.com',
    'chromeos-lkgm@google.com',
]

BOT_AUTHORS = [
    'eseidel@chromium.org', # Blink AutoRollBot (uses the CQ).
    'ojan@chromium.org', # Blink AutoRebaselineBot, commits before sending mail.
]

REPOSITORIES = [
    {
        'name': 'chrome',
        'relative_path': '.',
        'svn_url': 'svn://svn.chromium.org/chrome/trunk/src',
        'branch_heads': 'refs/remotes/branch-heads',
    },
    {
        'name': 'blink',
        'relative_path': 'third_party/WebKit',
        'svn_url': 'svn://svn.chromium.org/blink/trunk',
        'branch_heads': 'refs/remotes/branch-heads/chromium',
    },
    # Skia's branches don't seem to follow the expected pattern:
    # https://code.google.com/p/skia/source/browse/#svn%2Fbranches%2Fchrome
    # {
    #     'name': 'skia',
    #     'relative_path': 'third_party/skia/src',
    #     'svn_url': 'http://skia.googlecode.com/svn/trunk/src',
    #     'branch_heads': 'refs/remotes/branch-heads/chrome'
    # },
    # V8 also has its own branch pattern (they have separate releases)
    # but we could use the DEPS files for chrome for both Skia and V8
    # https://code.google.com/p/v8/source/browse#svn%2Fbranches
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


# Rietveld appears to use a serialization format with optional ms.
# e.g. https://codereview.chromium.org/api/202813005 'create' time
# or https://codereview.chromium.org/api/194383003?messages=true (messages[0]['date'])
def parse_rietveld_date(date_string):
    try:
        return parse_datetime_ms(date_string)
    except ValueError:
        return parse_datetime(date_string)


def parse_datetime_ms(date_string):
    # Microseconds just make debug printing ugly.
    return datetime.datetime.strptime(date_string, PYTHON_DATE_FORMAT_MS).replace(microsecond = 0)


def parse_datetime(date_string):
    return datetime.datetime.strptime(date_string, PYTHON_DATE_FORMAT)


def release_history(channel, os):
    # Query limit is 1000, so to go back far enough we need to query
    # each os/channel pair separately.
    url = RELEASE_HISTORY_CSV_URL + "?os=%s&channel=%s" % (os, channel)
    with requests_cache.disabled():
        history_text = requests.get(url).text
    lines = history_text.strip('\n').split('\n')
    expected_fields = ['os', 'channel', 'version', 'timestamp']
    releases = read_csv_lines(lines, expected_fields)
    return releases


def fetch_branch_release_times():
    release_times = {}
    # The win canary has been broken for multiple days at times.
    # So read both Win and Mac canary releases -- hitting either
    # canary will count as 'releasing'.
    releases = release_history('canary', 'win')
    relases += release_history('canary', 'mac')

    for release in releases:
        date = parse_datetime_ms(release['timestamp'])
        branch = release['version'].split('.')[2]
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
        response = requests.get(review_url, timeout=10)
        if not getattr(response, 'from_cache', False):
            log.debug("Hit network: %s" % review_url)
    except (requests.exceptions.Timeout, requests.exceptions.SSLError) as e:
        log.error('Timeout fetching %s' % review_url)
        return None

    try:
        return response.json()
    except ValueError, e:
        if "Sign in" in response.text:
            log.warn("%s is restricted" % review_url)
        elif "No issue exists with that id" in response.text:
            # e.g.  https://codereview.chromium.org/api/202303004?messages=true
            # from Chromium's bbee25f
            log.warn("%s was deleted" % review_url)
        else:
            log.error("Unknown error parsing %s (%s)" % (review_url, e))


def commit_times(commit_id, repository):
    change = {}
    args = ['git', 'log', '-1', '--pretty=format:%ct%n%cn%n%b', commit_id]
    log_text = subprocess.check_output(args, cwd=repository['relative_path'])

    lines = log_text.split("\n")
    change['commit_date'] = datetime.datetime.utcfromtimestamp(int(lines.pop(0)))
    change['commit_author'] = lines.pop(0)
    change['svn_revision'] = svn_revision_from_lines(lines, repository)
    change['review_id'] = review_id_from_lines(lines)
    return change


def review_times(review_id, commit_id):
    change = {}
    if not review_id:
        return change
    review = fetch_review(review_id)
    if not review:
        log.debug('Skipping %s, failed to fetch/parse review JSON.' % commit_id)
        return change

    change['review_create_date'] = parse_rietveld_date(review['created'])

    messages = review['messages']
    if not messages:
        log.error('Review %s from %s has 0 messages??' % (review_id, commit_id))

    change['review_sent_date'] = parse_rietveld_date(messages[0]['date']) if messages else None

    lgtms = [parse_rietveld_date(m['date']) for m in messages if m['approval']]
    change['lgtms'] = len(lgtms)
    change['first_lgtm_date'] = lgtms[0] if lgtms else None
    change['last_lgtm_date'] = lgtms[-1] if lgtms else None

    # We could also look for "The CQ bit was checked by" instead
    # but I'm not sure how long rietveld has been adding that.
    cq_starts = [parse_rietveld_date(m['date']) for m in messages if m['text'].startswith('CQ is trying da patch.')]
    change['cq_starts'] = len(cq_starts)
    change['first_cq_start_date'] = cq_starts[0] if cq_starts else None
    change['last_cq_start_date'] = cq_starts[-1] if cq_starts else None
    return change


default_fields = dict(zip(CSV_FIELD_ORDER, [None] * len(CSV_FIELD_ORDER)))

def change_times(commit_id, branch, repository, branch_release_times):
    change = default_fields.copy()
    change.update({
        'commit_id': commit_id,
        'repository': repository['name'],
        'branch': branch,
        'branch_release_date': branch_release_times.get(branch),
    })
    change.update(commit_times(commit_id, repository))
    change.update(review_times(change['review_id'], commit_id))
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
        log.warn("Latest local branch %s is older than latest released branch %s in \"%s\" (%s); running git fetch to update." %
            (latest_local_branch, latest_released_branch,
                repository['relative_path'], repository['name']))
        subprocess.check_call(['git', 'fetch'], cwd=repository_path)


def csv_path(branch, repository):
    return os.path.join(CACHE_NAME, '%s_%s.csv' % (branch, repository['name']))


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
            branch_names = filtered_branches
    return branch_names


def load_cached_branches(args, branch_release_times):
    cache_paths = glob.glob(os.path.join(CACHE_NAME, '*.csv'))
    cached_branches = set()
    for cache_path in cache_paths:
        name = os.path.basename(cache_path)
        match = CACHE_FILE_REGEXP.match(name)
        if not match:
            log.warn('%s does not match cache pattern, ignoring.' % cache_path)
        else:
            branch = match.group('branch')
            if not branch_release_times.get(branch):
                if args.prune:
                    log.info('cached branch %s (from %s) is not in released branches, removing.' % (branch, cache_path))
                    os.unlink(cache_path)
                    continue
                else:
                    log.warn('cached branch %s (from %s) is not in released branches! (pass --prune to remove)' % (branch, cache_path))
            cached_branches.add(branch)
    log.info("%s files for %s branches in cache." % (len(cache_paths), len(cached_branches)))
    return cached_branches


def skia_revision_for(branch):
    args = [
        'git',
        'show',
        'refs/remotes/branch-heads/%s:DEPS' % branch,
    ]
    deps = subprocess.check_output(args)
    skia_regexp = re.compile(r'\s*"skia_hash": "(?P<hash>\w+)",')
    for line in deps.split('\n'):
        match = skia_regexp.match(line)
        if match:
            return match.group('hash')


def update_command(args):
    branch_release_times = fetch_branch_release_times()
    branch_names = validate_checkouts_and_fetch_branch_names(branch_release_times)
    # FIXME: Instead of updating all branches we happen to have cached
    # it might make more sense to take a --since-branch argument and fetch/update
    # all branches since that one.
    cached_branches = load_cached_branches(args, branch_release_times)

    branch_count = min(len(branch_names) - 1, args.branch_count)
    cache_hits = 0

    if not os.path.exists(CACHE_NAME):
        print "Empty cache, creating: %s" % CACHE_NAME
        os.makedirs(CACHE_NAME)

    branches = set()

    if args.branch:
        branches.add(args.branch)
    else:
        branches.update(branch_names[:branch_count])
        branches.update(cached_branches)

    # Note: This depends on using integer branch names which may break.
    for branch in sorted(branches, key=int, reverse=True):
        if not branch_release_times.get(branch):
            log.error("No release date for %s, validate_checkouts_and_fetch_branch_names should have caught this??" % branch)
            continue

        # print skia_revision_for(branch)

        branch_index = branch_names.index(branch)
        previous_branch = branch_names[branch_index + 1] if branch_index < len(branch_names) else None

        for repository in REPOSITORIES:
            cache_path = csv_path(branch, repository)
            commits = commits_new_in_branch(branch, previous_branch, repository)

            # FIXME: Need more sophisticated validatation:
            # Warn about files which exist but don't have a corresponding branch?
            if not args.force and os.path.exists(cache_path):
                filename = os.path.basename(cache_path)
                records = read_csv(cache_path, CSV_FIELD_ORDER)
                if records is None:
                    log.debug("%s invalid, refetching." % filename)
                    sys.stderr.write('R')
                    sys.stderr.flush()
                elif len(records) != len(commits):
                    log.warn('%s has wrong number of commits (got: %s expected %s), refetching.' % (filename, len(records), len(commits)))
                else:
                    sys.stderr.write('.')
                    sys.stderr.flush()
                    cache_hits += 1
                    continue

            with open(cache_path, "w") as csv_file:
                csv_file.write(",".join(CSV_FIELD_ORDER) + "\n")
                log.info("%s commits between branch %s and %s in %s" %
                    (len(commits), branch, previous_branch, repository['name']))
                for commit_id in commits:
                    change = change_times(commit_id, branch, repository, branch_release_times)
                    if change:
                        csv_file.write(csv_line(change, CSV_FIELD_ORDER) + "\n")
    print "\nChecked %s branches, %s were already in cache." % (len(branches) * len(REPOSITORIES), cache_hits)


def split_csv_line(csv_line):
    return csv_line.strip('\n').split(',')


def read_csv_lines(lines, expected_fields=None):
    fields = split_csv_line(lines[0])
    if expected_fields and fields != expected_fields:
        # FIXME: This should probably be an exception?
        log.debug("CSV Field mismatch, got: %s, expected: %s" %
            (fields, expected_fields))
        return None

    return [dict(map(_convert_dates, zip(fields, split_csv_line(line)))) for line in lines[1:]]


def read_csv(file_path, expected_fields=None):
    with open(file_path) as csv_file:
        return read_csv_lines(list(csv_file), expected_fields)


def seconds_between_keys(change, earlier_key, later_key, clamp_values=True):
    earlier_date = change[earlier_key]
    later_date = change[later_key]
    if earlier_date is None or later_date is None:
        return 0 if clamp_values else None
    seconds = int((later_date - earlier_date).total_seconds())
    if clamp_values and later_date < earlier_date:
        # review_sent_date to commit_date is negative for all manual commits.
        if earlier_key != 'review_sent_date' or later_key != 'commit_date':
            log.info("Time between %s and %s in %s is negative (%s), ignoring." % (earlier_key, later_key, change['commit_id'], seconds))
        return 0
    return seconds


# Maybe this should be a "date trust order"?
def which_date_to_trust(before, after):
    if 'commit_date' in (before, after):
        return 'commit_date'
    if 'first_lgtm_date' == before:
        return after
    log.debug("Unhandled: %s vs %s, trusting %s" % (before, after, before))
    return before


def filter_bad_dates(change):
    change_copy = None
    last_good_event = GRAPH_ORDERED_EVENTS[0]
    for event_name in GRAPH_ORDERED_EVENTS[1:]:
        date = change[event_name]
        if not date:
            continue
        if date >= change[last_good_event]:
            last_good_event = event_name
            continue
        # Unclear if we need this hackish Copy-on-write.
        if not change_copy:
            change_copy = change.copy()
        trusted_event = which_date_to_trust(last_good_event, event_name)
        untrusted_event = last_good_event if trusted_event == event_name else event_name 
        log.info("%s unexpectedly before %s in %s, ignoring %s." % (event_name, last_good_event, change['commit_id'], untrusted_event))
        change_copy[untrusted_event] = None
        last_good_event = trusted_event
    return change_copy if change_copy else change


# http://stackoverflow.com/questions/9470611/how-to-do-an-inverse-range-i-e-create-a-compact-range-based-on-a-set-of-numb/9471386#9471386
def re_range(lst):
    def sub(x):
        return x[1] - x[0]

    ranges = []
    for k, iterable in itertools.groupby(enumerate(sorted(lst)), sub):
         rng = list(iterable)
         if len(rng) == 1:
             s = str(rng[0][1])
         else:
             s = "%s-%s" % (rng[0][1], rng[-1][1])
         ranges.append(s)
    return ', '.join(ranges)

def print_long_stats(changes, from_key, to_key):
    print "From: ", from_key
    print "To: ", to_key
    times = map(lambda change: seconds_between_keys(change, from_key, to_key), changes)
    print "Commits: ", len(times)
    print "Mean:", datetime.timedelta(seconds=int(numpy.mean(times)))
    print "Precentiles:"
    for percentile in (1, 10, 25, 50, 75, 90, 99):
        seconds = numpy.percentile(times, percentile)
        time_delta = datetime.timedelta(seconds=int(seconds))
        print "%s%%: %s" % (percentile, time_delta)


def print_oneline_stats(changes, from_key, to_key):
    unfiltered_times = map(lambda change: seconds_between_keys(change, from_key, to_key, clamp_values=False), changes)
    times = filter(lambda seconds: seconds >= 0, unfiltered_times)
    mean = datetime.timedelta(seconds=int(numpy.mean(times)))
    median = datetime.timedelta(seconds=int(numpy.median(times)))
    # Just mean and median.
    filtered_count = len(unfiltered_times) - len(times)
    filtered_percent = int(float(filtered_count) / len(times) * 100)
    print "%14s -> %14s %16s %16s  %s (%s%%)" % (from_key[:-5], to_key[:-5], median, mean, filtered_count, filtered_percent)


def _int_values(changes, value_name):
    values = map(operator.itemgetter(value_name), changes)
    return [int(value) for value in values if value is not None]


def print_stats(changes):
    # filtered_changes = map(filter_bad_dates, changes)
    print "Branches: ", re_range(sorted(set(map(lambda change: int(change['branch']), changes))))
    print "Dates: %s - %s" % (changes[0]['commit_date'], changes[-1]['commit_date'])
    review_less = filter(lambda change: not change['review_id'], changes)
    print "Commits: %s (%s w/o reviews)" % (len(changes), len(review_less))
    # print_long_stats(changes, 'review_sent_date', 'commit_date')
    lgtms = _int_values(changes, 'lgtms')
    print "LGTMs (in %s): mean: %.2f median: %s" % (len(lgtms), numpy.mean(lgtms), numpy.median(lgtms))
    cq_starts = _int_values(changes, 'cq_starts')
    print "CQ Starts (in %s): mean: %.2f median: %s" % (len(cq_starts), numpy.mean(cq_starts), numpy.median(cq_starts))

    print "%14s -> %14s %16s %16s  %s" % ('from', 'to', 'median', 'mean', 'ignored')
    for from_key, to_key in window(ALL_ORDERED_EVENTS):
        print_oneline_stats(changes, from_key, to_key)

    print_oneline_stats(changes, 'review_sent_date', 'commit_date')
    print_oneline_stats(changes, 'last_lgtm_date', 'commit_date')
    print_oneline_stats(changes, 'first_cq_start_date', 'commit_date')
    print_oneline_stats(changes, 'last_cq_start_date', 'commit_date')
    print_oneline_stats(changes, ALL_ORDERED_EVENTS[0], ALL_ORDERED_EVENTS[-1])
    print "'ignored' means an endpoint was missing (e.g. TBR= change) or time < 0 (e.g. CQ was tried before LGTM)"


def load_changes(repository=None, branch_limit=None, show_progress=True):
    changes = []
    pattern = '*.csv'
    if repository:
        pattern = '*_%s.csv' % repository

    paths = glob.glob(os.path.join(CACHE_NAME, pattern))
    stray_files = [path for path in paths if not CACHE_FILE_REGEXP.match(os.path.basename(path))]
    if stray_files:
        log.warn("Stray files in cache: %s" % stray_files)

    # FIXME: This is probably the least efficent way possible to implement this:
    if branch_limit:
        def branch_for(path):
            match = CACHE_FILE_REGEXP.match(os.path.basename(path))
            if not match:
                return None
            return int(match.group('branch'))
        branches = filter(None, map(branch_for, paths))
        branches = sorted(set(branches)) # Remove duplicates
        most_recent_branches = sorted(branches, key=int, reverse=True)[:int(branch_limit)]
        paths = filter(lambda path: branch_for(path) in most_recent_branches, paths)

    for path in paths:
        records = read_csv(path, CSV_FIELD_ORDER)
        if show_progress:
            #log.debug("%s changes in %s" % (len(records), path))
            sys.stderr.write('.')
            sys.stderr.flush()
        if records:
            changes.extend(records)
    return changes


def filter_bad_changes(changes):
    # FIXME: We may want to make filtering an explicit step?
    no_bots = filter(lambda change: change['commit_author'] not in BOT_AUTHORS, changes)
    for change in no_bots:
        # LGTMs after commit are common, but can just be ignored for our stats.
        if change['first_lgtm_date'] and change['first_lgtm_date'] > change['commit_date']:
            change['first_lgtm_date'] = None

    # if not change['review_id']:
    #     if change['commit_author'] not in NO_REVIEW_URL_AUTHORS:
    #         log.debug('Skipping %s from %s no Review URL' %
    #             (commit_id, change['commit_author']))
    #     return None
    return no_bots


def load_and_filter_changes(repository=None, branch_limit=None, show_progress=True):
    return filter_bad_changes(load_changes(repository, show_progress))


def stats_command(args):
    for repository in REPOSITORIES:
        changes = load_and_filter_changes(repository['name'], branch_limit=args.branch_limit)
        changes.sort(key=operator.itemgetter('svn_revision'))
        print "\nRepository: %s" % repository
        # print_stats may try to iterate over the iterator more than once, so make it a list.
        print_stats(list(changes))


def check_command(args):
    for repository in REPOSITORIES:
        changes = load_changes(repository['name'], branch_limit=args.branch_limit)
        changes.sort(key=operator.itemgetter('svn_revision'))
        print "\nRepository: %s" % repository['name']
        first_revision = changes[0]['svn_revision']
        last_revision = changes[-1]['svn_revision']
        missing_count = int(last_revision) - int(first_revision) - len(changes)
        print "%d changes %s:%s (missing %d)" % (len(changes), first_revision, last_revision, missing_count)
        # FIXME: What are these changes we're missing? All branch commits?
        # for first, second in window(changes):
        #     first_revision = int(first['svn_revision'])
        #     second_revision = int(second['svn_revision'])
        #     if (second_revision - first_revision) == 1:
        #         continue
        #     print "Missing", range(first_revision + 1, second_revision)


# FIXME: There must be a simpler way to write this.
def change_stats(change, ordered_events):
    results = {}
    reversed_names = list(reversed(ordered_events))
    for index, event_name in enumerate(reversed_names):
        date = change[event_name]
        if not date:
            results[event_name] = 0
            continue

        previous_index = index
        previous_date = None
        while previous_index + 1 < len(reversed_names) and not previous_date:
            previous_index += 1
            previous_name = reversed_names[previous_index]
            previous_date = change[previous_name]

        if not previous_date:
            results[event_name] = 0
            continue

        seconds = (date - previous_date).total_seconds()
        if seconds < 0:
            # FIXME: This isn't quite right, we should skip these entries entirely.
            # FIXME: This ignores the later event, when we probably should be ignoring
            # the previous event which came in late instead.
            # It's common to TBR a patch and get a belated LGTM, for instance, but we
            # should ignore the LGTM time instead of the CQ time.
            log.debug("Time between %s and %s in %s is negative (%s), ignoring." % (event_name, reversed_names[previous_index], change['commit_id'], seconds))
            seconds = 0
        results[event_name] = int(seconds / 60)
    # FIXME: Need to sanity check that the sum of these stats is equal to
    # seconds_between_keys(change, 'review_create_date', 'branch_release_date')
    return results


# http://stackoverflow.com/questions/312443/how-do-you-split-a-list-into-evenly-sized-chunks-in-python
def chunks(l, n):
    """ Yield successive n-sized chunks from l."""
    for i in xrange(0, len(l), n):
        yield l[i:i+n]


def _json_list(stats, change, ordered_events):
    return [int(change['svn_revision'])] + map(lambda name: int(stats[name]), ordered_events[1:])


# http://stackoverflow.com/questions/6998245/iterate-over-a-window-of-adjacent-elements-in-python
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


def diff_names(ordered_events):
    # remove "_date" from the end of each name.
    return ['%s_to_%s' % (first[:-5], second[:-5]) for first, second in window(ordered_events)]


def remove_outliers(lists, std_devs=2):
    means = numpy.mean(lists, axis=0)
    stds = numpy.multiply(numpy.array([std_devs] * len(lists[0])), numpy.std(lists, axis=0))
    # data - np.mean(data)) < m * np.std(data)
    # We only want the data if all of the parts are < N std_devs from the mean.
    # Which means abs(data - means) - (N * std_devs) < 0.
    return filter(lambda data: all(numpy.signbit(numpy.subtract(numpy.absolute(numpy.subtract(data, means)), stds))), lists)


def graph_command(args):
    # FIXME: chunk_size should be controlable via an argument.
    chunk_size = 10
    ordered_events = GRAPH_ORDERED_EVENTS

    for repository in REPOSITORIES:
        changes = load_and_filter_changes(repository['name'])
        changes.sort(key=operator.itemgetter('svn_revision'))
        print "window.%s_stats = [" % repository
        print ['svn_revision'] + diff_names(ordered_events), ","
        json_lists = [_json_list(change_stats(change, ordered_events), change, ordered_events) for change in changes]
        # FIXME: It's possible that remove_outliers is removing records based on SVN revision.
        json_lists = remove_outliers(json_lists)
        # It's a bit odd to avg svn revisions, but whatever.
        avg_jsons = [map(int, list(numpy.mean(chunk, axis=0))) for chunk in chunks(json_lists, chunk_size)]
        for json in avg_jsons:
            print json, ", "
        print "];"


def debug_command(args):
    repository = next(r for r in REPOSITORIES if r['name'] == args.repository_name)
    change = commit_times(args.commit_id, repository)
    for key in CSV_FIELD_ORDER:
        print key, change.get(key)

    print
    change['branch_release_date'] = change['commit_date'] # Prevent exception.
    stats = change_stats(change, GRAPH_ORDERED_EVENTS)
    for key in GRAPH_ORDERED_EVENTS:
        print key, stats.get(key)


def main(args):
    # CAREFUL: This caches everything, including omaha proxy lookups!
    requests_cache.install_cache(CACHE_NAME)

    parser = argparse.ArgumentParser()
    parser.add_argument('chrome_path')
    parser.add_argument('--verbose', '-v', action='store_true')
    subparsers = parser.add_subparsers()

    update_parser = subparsers.add_parser('update')
    update_parser.add_argument('--force', action='store_true')
    update_parser.add_argument('--branch-count', default=20, type=int)
    update_parser.add_argument('--branch', action='store')
    update_parser.add_argument('--prune', action='store_true')
    update_parser.set_defaults(func=update_command)

    stats_parser = subparsers.add_parser('stats')
    stats_parser.set_defaults(func=stats_command)
    stats_parser.add_argument('--branch-limit', default=None, type=int)

    graph_parser = subparsers.add_parser('graph')
    graph_parser.set_defaults(func=graph_command)

    check_parser = subparsers.add_parser('check')
    check_parser.set_defaults(func=check_command)
    check_parser.add_argument('--branch-limit', default=None, type=int)

    debug_parser = subparsers.add_parser('debug')
    debug_parser.set_defaults(func=debug_command)
    debug_parser.add_argument('repository_name')
    debug_parser.add_argument('commit_id')

    args = parser.parse_args(args)

    global logging_handler
    level = logging.DEBUG if args.verbose else logging.WARN
    logging_handler.setLevel(level)

    # This script assume's its being run from the root of a chrome checkout
    # we could remove this restriction by fixing uses of the REPOSITORIES
    # relative_path key.
    os.chdir(args.chrome_path)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
