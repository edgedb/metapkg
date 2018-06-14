#!/usr/bin/env python3

import argparse
import os
import pathlib
import shutil
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'install_list',
        help='List of files to be installed.')
    parser.add_argument(
        'no_install_list',
        help='List of files that are intentionally not installed.')
    parser.add_argument(
        'install_dir',
        help='Installation directory.')

    args = parser.parse_args()

    install_dir = pathlib.Path(args.install_dir)

    with open(args.install_list, 'r') as f:
        install_set = {l.strip() for l in f}

    with open(args.no_install_list, 'r') as f:
        no_install_set = {l.strip() for l in f}

    to_remove = no_install_set - install_set

    for path in sorted(to_remove, reverse=True):
        full_path = install_dir / path
        print('Removing {}'.format(path))
        if full_path.is_dir():
            shutil.rmtree(str(full_path))
        elif full_path:
            os.unlink(str(full_path))


if __name__ == '__main__':
    sys.exit(main())
