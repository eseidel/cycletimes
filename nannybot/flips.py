# Take builders file
# test-results.appspot.com/builders

# For each master, for each builder, for each testtype
# test-results.appspot.com/testfile?master=ChromiumWebkit&testtype=layout-tests&name=results.json&builder=WebKit Linux 32

# flips

import requests
import buildbot
import reasons

import requests
import requests_cache
import json
import collections
import sys
import argparse


BUILDERS = "http://test-results.appspot.com/builders"
builder_json = requests.get(BUILDERS).json()

requests_cache.install_cache('flips')


def convert_trie_to_flat_paths(trie, prefix=None):
    # Cloned from webkitpy.layout_tests.layout_package.json_results_generator
    # so that this code can stand alone.
    result = {}
    for name, data in trie.iteritems():
        if prefix:
            name = prefix + "/" + name

        if len(data) and not "results" in data:
            result.update(convert_trie_to_flat_paths(data, name))
        else:
            result[name] = data

    return result

RESULT_TYPES = {
    'A': 'AUDIO',
    'C': 'CRASH',
    'F': 'TEXT',
    'I': 'IMAGE',
    'K': 'LEAK',
    'L': 'FLAKY',
    'O': 'MISSING',
    'N': 'NO DATA',
    'Q': 'FAIL',
    'P': 'PASS',
    'T': 'TIMEOUT',
    'Y': 'NOTRUN',
    'X': 'SKIP',
    'Z': 'IMAGE+TEXT'
}


def ignore_missing_results(results):
    # result is a tuple (count, type)
    filtered = []
    for result_tuple in results:
        if result_tuple[1] in ('N', 'O'):
            continue
        if filtered and filtered[-1][1] == result_tuple[1]:
            filtered[-1][0] += result_tuple[0]
        else:
            filtered.append(result_tuple)
    return filtered


def crawl_command(args):
    flips_by_test = collections.defaultdict(list)

    flip_records = 0
    success_count = 0
    crawled_urls = set()

    # builder_json['no_upload_test_types']
    for index, test_group in enumerate(builder_json['masters'], 1):
        master_name = test_group['url_name']
        print '%s (%s of %s) %s test groups' % (master_name, index,
            len(builder_json['masters']), len(test_group['tests']))
        # [u'tests', u'url_name', u'name', u'groups']
        for step_name, builder_group in test_group['tests'].items():
            # FIXME: Sometimes builder names are duplicated?
            builder_names = sorted(set(builder_group['builders']))
            for builder_name in builder_names:
                params = {
                  'master': reasons.fancy_case_master_name(master_name),
                  'builder': builder_name,
                  'testtype': step_name,
                  'name': 'results.json',
                }
                base_url = 'http://test-results.appspot.com/testfile'
                response = requests.get(base_url, params=params)
                if response.url in crawled_urls:
                    print "Error, crawled twice: %s" % response.url
                crawled_urls.add(response.url)
                if response.status_code != 200:
                    continue
                success_count += 1
                results_json = response.json()[builder_name]
                tests = convert_trie_to_flat_paths(results_json['tests'])
                for test_name, outputs in tests.items():
                    results = ignore_missing_results(outputs['results'])
                    result_groups = len(results)
                    if result_groups <= 5:
                        continue

                    for result in results:
                        print result[1] * result[0],
                    print
                    flakes = (result_groups - 1) / 2
                    key = '%s:%s' % (step_name, test_name)
                    value = (
                        master_name,
                        builder_name,
                        flakes,
                    )
                    flips_by_test[key].append(value)

    print len(flips_by_test.keys()), flip_records, len(crawled_urls), success_count

    with open('all_results.json', 'w') as all_file:
        json.dump(flips_by_test, all_file)

    print "Wrote all results to all_results.json"


def process_command(args):
    with open('all_results.json') as all_file:
        results_json = json.load(all_file)
    print len(results_json.keys())

    types = collections.Counter([key.split(':')[0] for key in results_json.keys()])

    for key, count in types.most_common():
        print key, count


def main(args):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    update_parser = subparsers.add_parser('crawl')
    update_parser.set_defaults(func=crawl_command)

    update_parser = subparsers.add_parser('process')
    update_parser.set_defaults(func=process_command)

    args = parser.parse_args(args)
    return args.func(args)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
