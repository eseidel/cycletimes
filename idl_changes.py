#!/usr/bin/env python
import os
import sys
import subprocess
import re
import itertools
import argparse
import tempfile

BLINK_PATH = '/src/chromium/src/third_party/WebKit'
SOURCE_PATH = os.path.join(BLINK_PATH, 'Source')
sys.path.insert(0, SOURCE_PATH)

from bindings.scripts import idl_reader


# FIXME: Share with cycletimes.py
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


def find_idl_files(idl_root):
    idl_paths = []
    for root, dirs, files in os.walk(idl_root):
        for file_name in files:
            _, ext = os.path.splitext(file_name)
            if ext != '.idl':
                continue
            path = os.path.join(root, file_name)
            idl_paths.append(path)
    return idl_paths


def load_interfaces(idl_paths):
    interfaces = []
    reader = idl_reader.IdlReader()
    for path in idl_paths:
        try:
            definitions = reader.read_idl_file(path)
        except:
            print "ERROR processing %s" % path
        interfaces.extend(definitions.interfaces.values())
    return interfaces


def paths_on_branch(repository, branch_name):
    branch_path = os.path.join(repository['branch_heads'], branch_name)
    args = [
        'git', 'ls-tree', '-r', '--name-only', branch_path
    ]
    return subprocess.check_output(args, cwd=repository['relative_path']).splitlines()


def file_contents_from_branch(repository, branch, file_path):
    branch_path = os.path.join(repository['branch_heads'], branch)
    args = [
        'git', 'show', '%s:%s' % (branch_path, file_path)
    ]
    return subprocess.check_output(args, cwd=repository['relative_path'])


def load_interfaces_from_branch(repository, branch):
    paths = paths_on_branch(repository, branch)
    idl_paths = filter(lambda path: path.endswith('.idl'), paths)

    interfaces = []
    reader = idl_reader.IdlReader()
    for path in idl_paths:
        try:
            # FIXME: We need a non-path API from IDLParser.
            with tempfile.NamedTemporaryFile() as hack:
                contents = file_contents_from_branch(repository, branch, path)
                hack.write(contents)
                hack.flush()
                definitions = reader.read_idl_file(hack.name)
            interfaces.extend(definitions.interfaces.values())
        except Exception, e:
            print "ERROR (%s) processing %s" % (e, path)

    return interfaces


def operation_string(op):
    string = op.name
    arg_strings = []
    for arg in op.arguments:
        arg_string = ''
        if arg.idl_type:
            arg_string += arg.idl_type.base_type
        else:
            arg_string += 'any'
        if arg.default_value:
            arg_string += '=%s' % arg.default_value
        arg_strings.append(arg_string)
    return string + '(%s)' % ', '.join(arg_strings)


def strings_for_interface(interface):
    strings = []
    strings.extend([interface.name + '.' + operation_string(con) for con in face.constructors])
    for attribute in interface.attributes:
        strings.append('%s.%s' % (interface.name, attribute.name))
    for constant in interface.constants:
        strings.append('%s.%s = %s' % (interface.name, constant.name, constant.value))
    strings.extend([interface.name + '.' + operation_string(op) for op in interface.operations])
    return strings


def main(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('--branch-limit', default=None, type=int)
    args = parser.parse_args(args)

    blink = {
        'relative_path': BLINK_PATH,
        'branch_heads': 'refs/remotes/branch-heads/chromium',
    }
    # FIXME: We should walk releases not just every branch,
    # like how audit_runtime_enabled_feature.py does.
    branches = fetch_recent_branches(blink)

    if args.branch_limit:
        branches = branches[:args.branch_limit]

    previous_strings = None
    for branch in branches:
        interfaces = load_interfaces_from_branch(blink, branch)
        print interfaces

    return 1

    idl_paths = find_idl_files(SOURCE_PATH)
    interfaces = load_interfaces(idl_paths)
    string_lists = map(strings_for_interface, interfaces)
    strings = sorted(itertools.chain(string_lists))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))