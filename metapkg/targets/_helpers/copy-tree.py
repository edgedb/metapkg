#!/usr/bin/env python3
from __future__ import annotations
from typing import *

import argparse
import logging
import os
import pathlib
import platform
import shutil
import stat
import sys


logger = logging.getLogger("copy-tree")
system = platform.system()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copies a tree of files to an empty directory."
    )
    parser.add_argument(
        "src",
        help=(
            "Source directory. To only add the contents of this directory,"
            " append / at the end."
        ),
    )
    parser.add_argument(
        "dest", help="Destination directory. Created if doesn't exist."
    )
    parser.add_argument(
        "--files-from",
        help="Optional list of files to copy from the source directory.",
    )
    parser.add_argument(
        "--flatten",
        help="Copy all files to the top directory.",
        action="store_true",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Show information on each file copied, directory made, etc.",
        action="store_true",
    )
    return parser.parse_args()


def die(msg: str) -> NoReturn:
    logger.error(msg)
    sys.exit(1)


def main(
    src: str,
    dest: str,
    *,
    files_from: Optional[str],
    flatten: bool,
) -> None:
    dest = ensure_destination(src, dest)
    all_files = list(ensure_relative(get_paths_in(src), src))

    if files_from:
        p = pathlib.Path(files_from)
        relative_files = list(ensure_relative(p.read_text().splitlines(), src))
        relative_files = add_missing_directory_entries(relative_files)
        logger.info(
            f"Using file list in {p} with {len(relative_files)} entries"
        )
        warn_about_excluded_files(included=relative_files, all_files=all_files)
        copy_files(src, dest, relative_files, flatten=flatten)
    else:
        logger.info(
            f"No file list given, copying all {len(all_files)} entries"
        )
        copy_files(src, dest, all_files, flatten=flatten)


def ensure_destination(src: str, dest: str) -> str:
    src_p = pathlib.Path(src)
    dest_p = pathlib.Path(dest)
    if not src.endswith(os.sep):
        # To mimic rsync behavior
        dest_p = dest_p / src_p.name
    if dest_p.exists():
        if not dest_p.is_dir():
            raise ValueError(f"{dest} is not a directory, cannot continue")
        if os.listdir(dest):
            logger.warning(f"Directory {dest} is not empty")
    else:
        os.makedirs(dest_p)  # no error handling, irrecoverable
    return str(dest_p)


def get_paths_in(directory: str) -> Iterator[str]:
    for root, dirs, files in os.walk(directory):
        root_p = pathlib.Path(root).relative_to(directory)
        for name in dirs:
            yield str(root_p / name)
        for name in files:
            yield str(root_p / name)


def ensure_relative(files: Iterable[str], root: str) -> Iterator[str]:
    root_p = pathlib.Path(root).resolve()
    for path in files:
        p = pathlib.Path(path)
        if p.is_absolute():
            p_treated_as_relative = root_p / str(p)[1:]
            if p_treated_as_relative.exists():
                p = p_treated_as_relative
            yield str(p.relative_to(root_p))
            continue

        abs_p = root_p / p
        if abs_p.is_symlink() or abs_p.exists():
            # a symlink might point to a non-existent file
            yield path
            continue

        if p.parts[0] == root_p.name:
            # file list element looks "off-by-one", created
            # outside of the directory given as `src` to the tool
            lose_one_level = p.relative_to(root_p.name)
            if (root_p / lose_one_level).exists():
                yield str(lose_one_level)
                continue

        logger.error(f"File in file list doesn't exist: {path}")


def copy_files(
    src: str,
    dest: str,
    files: Iterable[str],
    *,
    flatten: bool,
) -> None:
    """Copy files listed in `files` from `src` to `dest`.

    Paths in `files` must be relative.
    """
    src_dir = pathlib.Path(src)
    dest_dir = pathlib.Path(dest)
    basenames = {}
    for src_file in files:
        path_from = src_dir / src_file

        if flatten:
            if path_from.is_dir():
                logger.info(f"Skipping directory {src_file} due to --flatten")
                continue
            if path_from.is_symlink():
                logger.info(f"Skipping symlink {src_file} due to --flatten")
                continue

            basename = path_from.name
            if basename in basenames:
                die(
                    f"`copy-tree --flatten` encountered a duplicate file name:"
                    f" {src_file} and {basenames[basename]}"
                )
            basenames[basename] = src_file
            dest_file = basename
        else:
            dest_file = src_file

        path_to = dest_dir / dest_file
        if path_from.is_dir():
            if path_to.is_dir():
                logger.warning(f"Directory {path_to} already exists")
            else:
                try:
                    os.makedirs(path_to)
                except OSError as ose:
                    logger.error(
                        f"Failed making the {path_to} directory: {ose}"
                    )
                else:
                    logger.info(f"mkdir {path_to}")
        else:
            if path_to.exists():
                logger.warning(
                    f"File {path_to} already exists and will be overwritten"
                )
            try:
                shutil.copyfile(path_from, path_to, follow_symlinks=False)
            except Exception as e:
                logger.error(f"Failed copying {path_from} -> {path_to}: {e}")
            else:
                logger.info(f"cp {path_from} -> {path_to}")
        stat_from = path_from.lstat()
        stat_to = path_to.lstat()
        new_mode = stat_to.st_mode
        for mode in (stat.S_IXUSR, stat.S_IXGRP, stat.S_IXOTH):
            if stat_from.st_mode & mode:
                new_mode |= mode
        if new_mode != stat_to.st_mode:
            try:
                path_to.chmod(new_mode)
            except OSError as ose:
                logger.error(
                    f"Failed chmodding {path_to} to {oct(new_mode)}: {ose}"
                )
            else:
                logger.info(f"chmod {oct(new_mode)} {path_to}")

        if system != "Windows":
            try:
                os.utime(
                    path_to,
                    (stat_from.st_atime, stat_from.st_mtime),
                    follow_symlinks=False,
                )
            except (OSError, NotImplementedError) as ose:
                logger.error(f"Failed setting times on {path_to}: {ose}")
            else:
                pass  # logging `touch -t` is overly verbose


def warn_about_excluded_files(
    included: Collection[str], all_files: Collection[str]
) -> None:
    last_seen = ""

    def maybe_warn() -> None:
        if last_seen != "":
            logger.warning(f"Not in file list: {last_seen}")

    for file in sorted(set(all_files) - set(included)):
        skip = last_seen.endswith("/") and file.startswith(last_seen)
        if not skip:
            maybe_warn()
        last_seen = file
    maybe_warn()


def add_missing_directory_entries(files: Iterable[str]) -> List[str]:
    dirs: Set[pathlib.Path] = {pathlib.Path(".")}
    result: Set[str] = set()
    for file in files:
        if file.endswith("/"):
            file = file[:-1]
        for parent in reversed(pathlib.Path(file).parents):
            if parent not in dirs:
                dirs.add(parent)
                result.add(str(parent))
        result.add(file)
    return list(sorted(result))


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s - %(levelname)s: %(message)s",
    )
    main(args.src, args.dest, files_from=args.files_from, flatten=args.flatten)
