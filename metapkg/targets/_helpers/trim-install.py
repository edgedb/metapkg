#!/usr/bin/env python3

import argparse
import os
import pathlib
import shutil
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("install_list", help="List of files to be installed.")
    parser.add_argument(
        "no_install_list",
        help="List of files that should not be installed "
        "even if in install list.",
    )
    parser.add_argument(
        "ignore_list",
        help="List of files that are intentionally not installed.",
    )
    parser.add_argument("install_dir", help="Installation directory.")

    args = parser.parse_args()

    install_dir = pathlib.Path(args.install_dir)

    with open(args.install_list, "r") as f:
        install_set = {l.strip() for l in f}

    with open(args.no_install_list, "r") as f:
        no_install_set = {l.strip() for l in f}

    with open(args.ignore_list, "r") as f:
        ignore_set = {l.strip() for l in f}

    to_remove = (ignore_set - install_set) | no_install_set

    for path in sorted(to_remove, reverse=True):
        full_path = install_dir / path
        print("Removing {}".format(path), file=sys.stderr)
        if full_path.is_dir() and not full_path.is_symlink():
            shutil.rmtree(str(full_path))
        elif full_path.exists() or full_path.is_symlink():
            os.unlink(str(full_path))

    for path in install_set - no_install_set:
        print(path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
