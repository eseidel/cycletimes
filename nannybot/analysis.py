# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json
import collections
import operator


# Git ready, but only implemented for SVN atm.
def ids_after_first_including_second(first, second):
    if not first or not second:
        return []
    try:
        return range(int(first) + 1, int(second) + 1)
    except ValueError, e:
        # likely passed a git hash
        return []


# Git ready, but only implemented for SVN atm.
def is_ancestor_of(older, younger):
    return int(older) < int(younger)


def is_decendant_of(younger, older):
    return is_ancestor_of(older, younger)


def flatten_to_commit_list(passing, failing):
    # Flatten two commit dicts to a list of 'name:commit'
    if not passing or not failing:
        return []
    all_commits = []
    for name in passing.keys():
        commits = ids_after_first_including_second(passing[name], failing[name])
        all_commits.extend(['%s:%s' % (name, commit) for commit in commits])
    return all_commits


def lookup_and_compare(existing, new, compare):
    if not existing or compare(existing, new):
        return new
    return existing

# FIXME: Perhaps this should be done by the feeder?
def assign_keys(alerts):
    for key, alert in enumerate(alerts):
        # We could come up with something more sophisticated if necessary.
        alert['key'] = 'f%s' % key # Just something so it doesn't look like a number.
    return alerts


# FIXME: Perhaps this would be cleaner as:
# passing = find_maximal(alert, 'passing_revisions', is_ancestor_of)
# failing = find_maximal(alert, 'failing_revisions', is_decendant_of)
def merge_regression_ranges(alerts):
    last_passing = {}
    first_failing = {}
    for alert in alerts:
        passing = alert['passing_revisions']
        if not passing:
            continue
        failing = alert['failing_revisions']
        for name in passing.keys():
            last_passing[name] = lookup_and_compare(last_passing.get(name), passing.get(name), is_ancestor_of)
            first_failing[name] = lookup_and_compare(first_failing.get(name), failing.get(name), is_decendant_of)
    return last_passing, first_failing


def reason_key_for_alert(alert):
    # FIXME: May need something smarter for reason_key.
    reason_key = alert['step_name']
    if alert['piece']:
        reason_key += ':%s' % alert['piece']
    return reason_key


def group_by_reason(alerts):
    by_reason = collections.defaultdict(list)
    for alert in alerts:
        by_reason[reason_key_for_alert(alert)].append(alert)

    reason_groups = []
    for reason_key, alerts in by_reason.items():
        last_passing, first_failing = merge_regression_ranges(alerts)
        blame_list = flatten_to_commit_list(last_passing, first_failing)
        reason_groups.append({
            'sort_key': reason_key,
            'likely_revisions': blame_list,
            # FIXME: These should probably be a list of keys
            # once alerts have keys.
            'failure_keys': map(operator.itemgetter('key'), alerts),
        })
    return reason_groups

# http://stackoverflow.com/questions/18715688/find-common-substring-between-two-strings
def longestSubstringFinder(string1, string2):
    answer = ""
    len1, len2 = len(string1), len(string2)
    for i in range(len1):
        match = ""
        for j in range(len2):
            if (i + j < len1 and string1[i + j] == string2[j]):
                match += string2[j]
            else:
                if (len(match) > len(answer)): answer = match
                match = ""
    return answer


def merge_by_range(reason_groups):
    by_range = {}
    for group in reason_groups:
        range_key = ' '.join(sorted(group['likely_revisions']))
        existing = by_range.get(range_key)
        if not existing:
            by_range[range_key] = group
            continue

        by_range[range_key] = {
            'sort_key': longestSubstringFinder(existing['sort_key'], group['sort_key']),
            'likely_revisions': existing['likely_revisions'],
            'failure_keys': sorted(set(existing['failure_keys'] + group['failure_keys'])),
        }

    return sorted(by_range.values(), key=operator.itemgetter('sort_key'))
