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
# Blink Rolls
# Reverts
# Could show what % of time was due to tree-closures by overlapping lgtm -> commit time with tree closure data?
# Could show what % of tiem was due to CQ by overlapping CQ data with lgtm -> commit time.
# Want to show what % of a typical patch time is spent waiting for X.

# Want a breakdown of what the median time spent in each phase is.
# To answer the question of what phases do we need to make faster.


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
    # Microseconds just make debug printing ugly.
    return datetime.datetime.strptime(date_string, PYTHON_DATE_FORMAT_MS).replace(microsecond = 0)


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
    response = requests.get(review_url)
    try:
        return response.json()
    except ValueError, e:
        if "Sign in" in response.text:
            log.error("%s is restricted" % review_url)
        else:
            log.error("Unknown error parsing %s (%s)" % (review_url, e))


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
    change['review_create_date'] = parse_datetime_ms(review['created'])
    if review['messages']:
        change['review_sent_date'] = parse_datetime_ms(review['messages'][0]['date'])
    else:
        log.error('Review %s from %s has 0 messages??' % (review_id, commit_id))
        change['review_sent_date'] = None 
    change['first_lgtm_date'] = first_lgtm_date(review)
    change['first_cq_start_date'] = first_cq_start_date(review)
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
            branch_names = filtered_branches
    return branch_names


def fetch_command(args):
    branch_release_times = fetch_branch_release_times()
    branch_names = validate_checkouts_and_fetch_branch_names(branch_release_times)

    branch_limit = min(len(branch_names) - 1, args.branch_limit)

    if not os.path.exists(CACHE_LOCATION):
        os.makedirs(CACHE_LOCATION)

    if args.branch:
        index = branch_names.index(args.branch)
        branch_pairs = [(branch_names[index], branch_names[index + 1])]
    else:
        branch_pairs = [(branch_names[index], branch_names[index + 1]) for index in range(branch_limit)]

    for branch, previous_branch in branch_pairs:
        if not branch_release_times.get(branch):
            log.error("No release date for %s, validate_checkouts_and_fetch_branch_names should have caught this??" % branch)
        for repository in REPOSITORIES:
            cache_path = csv_path(branch, repository)
            commits = commits_new_in_branch(branch, previous_branch, repository)

            # FIXME: Need more sophisticated validatation:
            # Warn about files which exist but don't have a corresponding branch?
            if not args.force and os.path.exists(cache_path):
                filename = os.path.basename(cache_path)
                records = read_csv(cache_path)
                if records is None:
                    log.warn("%s invalid, refetching." % filename)
                elif len(records) != len(commits):
                    log.warn('%s has wrong number of commits (got: %s expected %s), refetching.' % (filename, len(records), len(commits)))
                else:
                    sys.stderr.write('.')
                    sys.stderr.flush()
                    continue

            with open(cache_path, "w") as csv_file:
                csv_file.write(",".join(CSV_FIELD_ORDER) + "\n")
                log.info("%s commits between branch %s and %s in %s" %
                    (len(commits), branch, previous_branch, repository['name']))
                for commit_id in commits:
                    change = change_times(commit_id, branch, repository, branch_release_times)
                    if change:
                        csv_file.write(csv_line(change, CSV_FIELD_ORDER) + "\n")
    print # stderr has no newline after the '.' writes.


def split_csv_line(csv_line):
    return csv_line.strip('\n').split(',')


def read_csv(file_path):
    csv_file = open(file_path)
    fields = split_csv_line(csv_file.readline())
    if fields != CSV_FIELD_ORDER:
        log.error("CSV Field mismatch, got: %s, expected: %s" %
            (fields, CSV_FIELD_ORDER))
        return None

    return [dict(map(_convert_dates, zip(CSV_FIELD_ORDER, split_csv_line(line)))) for line in csv_file]


def seconds_between_keys(change, earlier_key, later_key):
    earlier_date = change[earlier_key]
    later_date = change[later_key]
    if earlier_date is None or later_date is None:
        return 0
    seconds = int((later_date - earlier_date).total_seconds())
    if later_date < earlier_date:
        # review_sent_date to commit_date is negative for all manual commits.
        if earlier_key != 'review_sent_date' or later_key != 'commit_date':
            log.warn("Time between %s and %s in %s is negative (%s), ignoring." % (earlier_key, later_key, change['commit_id'], seconds))
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


def print_stats(changes):
    from_key = 'review_sent_date'
    to_key = 'commit_date'
    # filtered_changes = map(filter_bad_dates, changes)
    filtered_changes = changes # filter_bad_dates doesn't work well enough yet.
    times = map(lambda change: seconds_between_keys(change, from_key, to_key), filtered_changes)
    print "From: ", from_key
    print "To: ", to_key
    print "Branches: ", re_range(sorted(set(map(lambda change: int(change['branch']), filtered_changes))))
    print "Commits: ", len(times)
    print "Mean:", datetime.timedelta(seconds=int(numpy.mean(times)))
    print "Precentiles:"
    for percentile in (1, 10, 25, 50, 75, 90, 99):
        seconds = numpy.percentile(times, percentile)
        time_delta = datetime.timedelta(seconds=int(seconds))
        print "%s%%: %s" % (percentile, time_delta)


def load_changes():
    changes = []
    for path in glob.iglob(os.path.join(CACHE_LOCATION, '*.csv')):
        records = read_csv(path)
        #log.debug("%s changes in %s" % (len(records), path))
        sys.stderr.write('.')
        sys.stderr.flush()
        if records:
            changes.extend(records)
    # FIXME: We may want to make filtering an explicit step?
    no_bots = filter(lambda change: change['commit_author'] not in BOT_AUTHORS, changes)

    # if not change['review_id']:
    #     if change['commit_author'] not in NO_REVIEW_URL_AUTHORS:
    #         log.debug('Skipping %s from %s no Review URL' %
    #             (commit_id, change['commit_author']))
    #     return None

    return no_bots


def stats_command(args):
    changes = load_changes()
    changes.sort(key=operator.itemgetter('repository', 'svn_revision'))
    for repository, per_repo_changes in itertools.groupby(changes, key=operator.itemgetter('repository')):
        print "\nRepository: %s" % repository
        # print_stats may try to iterate over the iterator more than once, so make it a list.
        print_stats(list(per_repo_changes))


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
    changes = load_changes()
    changes.sort(key=operator.itemgetter('repository', 'svn_revision'))
    for repository, per_repo_changes in itertools.groupby(changes, key=operator.itemgetter('repository')):
        print "window.%s_stats = [" % repository
        print ['svn_revision'] + diff_names(ordered_events), ","
        json_lists = [_json_list(change_stats(change, ordered_events), change, ordered_events) for change in per_repo_changes]
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
    parser = argparse.ArgumentParser()
    parser.add_argument('chrome_path')
    subparsers = parser.add_subparsers()

    fetch_parser = subparsers.add_parser('fetch')
    fetch_parser.add_argument('--force', action='store_true')
    fetch_parser.add_argument('--branch-limit', default=20)
    fetch_parser.add_argument('--branch', action='store')
    fetch_parser.set_defaults(func=fetch_command)

    stats_parser = subparsers.add_parser('stats')
    stats_parser.set_defaults(func=stats_command)

    graph_parser = subparsers.add_parser('graph')
    graph_parser.set_defaults(func=graph_command)

    debug_parser = subparsers.add_parser('debug')
    debug_parser.set_defaults(func=debug_command)
    debug_parser.add_argument('repository_name')
    debug_parser.add_argument('commit_id')

    args = parser.parse_args(args)
    # This script assume's its being run from the root of a chrome checkout
    # we could remove this restriction by fixing uses of the REPOSITORIES
    # relative_path key.
    os.chdir(args.chrome_path)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
