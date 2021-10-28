from __future__ import annotations

import os
import os.path
import pathlib
import platform

from metapkg import tools
from metapkg.targets import generic


class GenericLinuxBuild(generic.Build):
    def get_tool_list(self) -> list[str]:
        tools = super().get_tool_list()
        tools.append("linux-static-linkdriver-wrapper.sh")
        return tools

    def _get_global_env_vars(self) -> dict[str, str]:
        env = super()._get_global_env_vars()

        wrapper = self.sh_get_command(
            "linux-static-linkdriver-wrapper",
            relative_to="sourceroot",
        )

        machine = platform.machine().upper()
        env[
            f"CARGO_TARGET_{machine}_UNKNOWN_LINUX_GNU_LINKER"
        ] = f"$(ROOT)/{wrapper}"

        return env

    def _fixup_rpath(
        self, image_root: pathlib.Path, binary_relpath: pathlib.Path
    ) -> None:
        inst_prefix = self.get_full_install_prefix()
        full_path = image_root / binary_relpath
        inst_path = pathlib.Path("/") / binary_relpath
        rpath_record = tools.cmd(
            "patchelf", "--print-rpath", full_path
        ).strip()
        rpaths = []
        if rpath_record:
            for entry in rpath_record.split(os.pathsep):
                entry = entry.strip()
                if not entry:
                    continue

                if entry.startswith("$ORIGIN"):
                    # rpath is already relative
                    rpaths.append(entry)
                else:
                    rpath = pathlib.Path(entry)
                    if rpath.is_relative_to(inst_prefix):
                        rel_rpath = os.path.relpath(
                            rpath, start=inst_path.parent
                        )
                        rpaths.append(f"$ORIGIN/{rel_rpath}")
                    else:
                        print(
                            f"RPATH {entry} points outside of install image, "
                            f"removing"
                        )

        if rpaths:
            new_rpath_record = os.pathsep.join(rp for rp in rpaths)
            if new_rpath_record != rpath_record:
                tools.cmd(
                    "patchelf",
                    "--force-rpath",
                    "--set-rpath",
                    new_rpath_record,
                    full_path,
                )
        elif rpath_record:
            tools.cmd(
                "patchelf",
                "--remove-rpath",
                full_path,
            )

    def _strip(
        self, image_root: pathlib.Path, binary_relpath: pathlib.Path
    ) -> None:
        full_path = image_root / binary_relpath
        tools.cmd("strip", full_path)
