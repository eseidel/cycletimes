#!/usr/bin/env python

import subprocess
import datetime
import itertools
import collections
import numpy
import sys
import os


ROOT = '/src/chromium/src'
REPOSITORIES = [
	'.',
	'third_party/WebKit',
	'third_party/skia/src',
	'v8',
]


def _tuple_from_line(line):
	try:
		date_string, author = line.split('###')
	except Exception, e:
		print line
		print e
		raise
	date = datetime.datetime.utcfromtimestamp(int(date_string))
	return (date, author)


def main(args):
	for repository in REPOSITORIES:
		directory = os.path.join(ROOT, repository)
		print 
		print directory
		args = [
			'git',
			'log',
			'--pretty=format:%ct###%an',
		]
		log_text = subprocess.check_output(args, cwd=directory)
		tuples = [_tuple_from_line(line) for line in log_text.split('\n')]
		print ' '.join(['month   ', 'com', 'con', 'avg', 'med', '90%'])
		for month, values in itertools.groupby(tuples, key=lambda date_and_author: date_and_author[0].month):
			tuples_list = list(values)
			counter = collections.Counter(map(lambda data_and_author: data_and_author[1], tuples_list))
			month_string = tuples_list[0][0].strftime('%m/%Y')
			commits = len(tuples_list)
			contributors = len(counter)
			mean_per = numpy.mean(counter.values())
			median_per = numpy.median(counter.values())
			ninty = numpy.percentile(counter.values(), 90)
			print "%s %4d %3d %.1f %.1f %.1f" % (month_string, commits, contributors, mean_per, median_per, ninty)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
