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


def commit_compare(one, two):
    if is_decendant_of(one, two):
        return -1
    if is_ancestor_of(one, two):
        return 1
    return 0 # This is technically not right, since commits can be non-comparable.


def flatten_to_commit_list(passing, failing):
    # Flatten two commit dicts to a list of 'name:commit'
    if not passing or not failing:
        return []
    all_commits = []
    for name in passing.keys():
        commits = ids_after_first_including_second(passing[name], failing[name])
        all_commits.extend(['%s:%s' % (name, commit) for commit in commits])
    return all_commits


# FIXME: Perhaps this should be done by the feeder?
def assign_keys(alerts):
    for key, alert in enumerate(alerts):
        # We could come up with something more sophisticated if necessary.
        alert['key'] = 'f%s' % key # Just something so it doesn't look like a number.
    return alerts


def merge_regression_ranges(alerts):
    def make_merge_dicts(reducer):
        def merge_dicts(one, two):
            if not one or not two:
                return None
            reduction = {}
            for key in one.keys():
                reduction[key] = reducer(one[key], two[key])
            return reduction
        return merge_dicts

    # These don't handle the case where commits can't be compared.
    older_commit = lambda one, two: one if is_ancestor_of(one, two) else two
    younger_commit = lambda one, two: one if is_decendant_of(one, two) else two

    passing_dicts = map(operator.itemgetter('passing_revisions'), alerts)
    last_passing = reduce(make_merge_dicts(younger_commit), passing_dicts)

    failing_dicts = map(operator.itemgetter('failing_revisions'), alerts)
    first_failing = reduce(make_merge_dicts(older_commit), failing_dicts)

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
        # FIXME: blame_list isn't filtered yet, but should be.
        reason_groups.append({
            'sort_key': reason_key,
            'merged_last_passing': last_passing,
            'merged_first_failing': first_failing,
            'likely_revisions': blame_list,
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


def range_key_for_group(group):
    last_passing = group['merged_last_passing']
    first_failing = group['merged_first_failing']
    if last_passing:
        range_key = ' '.join(flatten_to_commit_list(last_passing, first_failing))
    else:
        # Even regressions where we don't know when they started can be
        # merged by our earliest known failure.
        parts = ['<=%s:%s' % (name, commit) for name, commit in first_failing.items()]
        range_key = ' '.join(parts)
    # sort_key is a heuristic to avoid merging failiures like
    # gclient revert + webkit_tests which just happened to pull
    # exact matching revisions when failing.
    return range_key + group['sort_key'][:3]


def merge_by_range(reason_groups):
    expected_keys = sorted(reason_groups[0].keys())
    by_range = {}
    for group in reason_groups:
        range_key = range_key_for_group(group)
        existing = by_range.get(range_key)
        if not existing:
            # Shallow copy of group.
            by_range[range_key] = dict(group)
            continue

        # FIXME: It's possible we don't want to merge two keys with nothing in common.
        # e.g. bot_update and
        # We only care about these two keys, the rest should be the same between all groups.
        # I guess we could assert that...
        by_range[range_key].update({
            'sort_key': longestSubstringFinder(existing['sort_key'], group['sort_key']),
            'failure_keys': sorted(set(existing['failure_keys'] + group['failure_keys'])),
        })

    return sorted(by_range.values(), key=operator.itemgetter('sort_key'))
