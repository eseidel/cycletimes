import requests
import requests_cache
import collections
import json
import sys
import argparse
import itertools


def crawl_command(args):
    requests_cache.install_cache('builder_stats')

    CBE_BASE = 'https://chrome-build-extract.appspot.com'
    MASTERS_URL = 'https://chrome-infra-stats.appspot.com/_ah/api/stats/v1/masters'
    master_names = requests.get(MASTERS_URL).json()['masters']

    builder_stats = []

    for master_name in master_names:
        cbe_master_url = '%s/get_master/%s' % (CBE_BASE, master_name)
        master_json = requests.get(cbe_master_url).json()
        # print master_json['slaves'].keys()
        for builder_name, builder_json in master_json['builders'].items():
            cbe_builds_url = '%s/get_builds' % CBE_BASE
            params = { 'master': master_name, 'builder': builder_name }
            response_json = requests.get(cbe_builds_url, params=params).json()
            builds = response_json['builds']
            if builds:
                finished_build = next(b for b in builds if b['eta'] is None)
                first_step_name = finished_build['steps'][0]['name']
            else:
                first_step_name = None
            builder_tuple = (master_name, builder_name, first_step_name, builder_json['slaves'])
            print builder_tuple
            builder_stats.append(builder_tuple)

    with open('builder_stats.json', 'w') as stats_file:
        json.dump(builder_stats, stats_file)


def recipe_stats_line(tuples, name):
    # Count step names
    first_steps = collections.Counter([t[2] for t in tuples])
    recipes = first_steps['steps']
    total = len(tuples)
    print '%-22s %s of %s (%d%%) builders' % (name, recipes, total, 100.0 * recipes / total)



def recipes_command(args):
    with open('builder_stats.json') as stats_file:
        builder_stats = json.load(stats_file)

    print 'Recipe-enabled builders:'
    for master_name, group in itertools.groupby(builder_stats, lambda t: t[0]):
        recipe_stats_line(list(group), master_name)

    recipe_stats_line(builder_stats, 'TOTAL')


def main(args):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    update_parser = subparsers.add_parser('crawl')
    update_parser.set_defaults(func=crawl_command)

    update_parser = subparsers.add_parser('recipes')
    update_parser.set_defaults(func=recipes_command)

    args = parser.parse_args(args)
    return args.func(args)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
