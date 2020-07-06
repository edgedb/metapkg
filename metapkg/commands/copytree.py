from __future__ import annotations
from typing import *

import logging
import os
import pathlib
import shutil
import stat

from . import base


logger = logging.getLogger('metapkg.copytree')


class CopyTree(base.Command):
    """Copy a tree of files.

    copytree
        { src : Source directory. }
        { dest : Destination directory. }
        { --files-from= : An optional file list. }
    """

    help = """Copies a tree of files."""

    _loggers = ['metapkg.copytree']

    def handle(self) -> None:
        src = self.argument('src')
        dest = self.argument('dest')
        files_from = self.option('files-from')

        dest = self.ensure_destination(src, dest)
        all_files = list(
            self.ensure_relative(self.get_paths_in(src), src)
        )

        if files_from:
            p = pathlib.Path(files_from)
            relative_files = list(
                self.ensure_relative(p.read_text().splitlines(), src)
            )
            logger.info(
                f"Using file list in {p} with {len(relative_files)} entries"
            )
            for file in set(all_files) - set(relative_files):
                logger.warning(f"Not in file list: {file}")
            self.copy_files(src, dest, relative_files)
        else:
            logger.info(
                f"No file list given, copying all {len(all_files)} entries"
            )
            self.copy_files(src, dest, all_files)

    def ensure_destination(self, src: str, dest: str) -> str:
        src_p = pathlib.Path(src)
        dest_p = pathlib.Path(dest)
        if dest_p.exists():
            if not dest_p.is_dir():
                raise ValueError(f"{dest} is not a directory, cannot continue")
            if os.listdir(dest):
                # We don't want to replicate rsync here.
                raise ValueError(f"{dest} is not empty, cannot continue")

        if not src.endswith(os.sep):
            # To mimic rsync behavior
            dest_p = dest_p / src_p.name
        os.makedirs(dest_p)
        return str(dest_p)

    def get_paths_in(self, directory: str) -> Iterator[str]:
        for root, dirs, files in os.walk(directory):
            root_p = pathlib.Path(root).relative_to(directory)
            for name in dirs:
                yield str(root_p / name) + "/"
            for name in files:
                yield str(root_p / name)

    def ensure_relative(
        self, files: Iterable[str], root: str
    ) -> Iterator[str]:
        root_p = pathlib.Path(root)
        for path in files:
            p = pathlib.Path(path)
            if p.is_absolute():
                yield str(p.relative_to(root_p))
            else:
                yield path

    def copy_files(self, src: str, dest: str, files: Iterable[str]) -> None:
        """Copy files listed in `files` from `src` to `dest`.

        Paths in `files` must be relative.
        """
        src_dir = pathlib.Path(src)
        dest_dir = pathlib.Path(dest)
        for file in files:
            path_from = src_dir / file
            path_to = dest_dir / file
            if path_from.is_dir():
                try:
                    os.makedirs(path_to)
                except OSError as ose:
                    logger.error(
                        f"Failed making the {path_to} directory: {ose}"
                    )
                else:
                    logger.info(f"mkdir {path_to}")
            else:
                try:
                    shutil.copyfile(path_from, path_to, follow_symlinks=False)
                except Exception as e:
                    logger.error(
                        f"Failed copying {path_from} -> {path_to}: {e}"
                    )
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
            try:
                os.utime(
                    path_to,
                    (stat_from.st_atime, stat_from.st_mtime),
                    follow_symlinks=False,
                )
            except OSError as ose:
                logger.error(f"Failed setting times on {path_to}: {ose}")
            else:
                pass  # logging `touch -t` is overly verbose
