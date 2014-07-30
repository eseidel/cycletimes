import requests
import collections
import json


MASTERS_URL = 'https://chrome-infra-stats.appspot.com/_ah/api/stats/v1/masters'
master_names = requests.get(MASTERS_URL).json()['masters']

builder_to_masters = collections.defaultdict(list)

for master_name in master_names:
    if 'tryserver' not in master_name:
        continue
    url_pattern = 'https://chrome-build-extract.appspot.com/get_master/%s'
    master_url = url_pattern % master_name
    master_json = requests.get(master_url).json()
    for builder_name in master_json['builders']:
        builder_to_masters[builder_name].append(master_name)


print json.dumps(builder_to_masters, indent=2)