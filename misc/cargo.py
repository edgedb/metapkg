import toml
import argparse
import csv
import pathlib
import sys


def main():
    csv.field_size_limit(sys.maxsize)

    parser = argparse.ArgumentParser()
    parser.add_argument("cargo_lock_file", type=str, help="path to Cargo.lock")
    parser.add_argument(
        "cargo_io_dump",
        type=str,
        help="path to decompressed crates.io db-dump "
        "(https://crates.io/data-access)",
    )

    args = parser.parse_args()

    data = toml.load(args.cargo_lock_file)
    db_path = pathlib.Path(args.cargo_io_dump) / "data"

    crates = {}
    crates_id_to_name = {}

    with open(db_path / "crates.csv", "rt", newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=",")
        header = next(reader)
        for row in reader:
            crate_info = dict(zip(header, row))
            crates[crate_info["name"]] = crate_info
            crates_id_to_name[crate_info["id"]] = crate_info["name"]

    with open(db_path / "versions.csv", "rt", newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=",")
        header = next(reader)
        for row in reader:
            crate_info = dict(zip(header, row))
            name = crates_id_to_name[crate_info["crate_id"]]
            lic = crate_info["license"]
            crates[name].setdefault("license", {})[crate_info["num"]] = lic

    failed_for = {}
    deps = {}
    for package in data["package"]:
        name, ver = package["name"], package["version"]

        if name not in crates:
            failed_for[name] = "not on crates.io"
        else:
            crate_data = crates[name]
            crate_license = crate_data["license"]
            try:
                deps[name] = crate_license[ver]
            except KeyError:
                failed_for[name] = f"could not find license for version {ver}"

    print("Dependencies:\n")
    for name, license in deps.items():
        print(f"{name}: {license}")
    if failed_for:
        print("\n\nFailed to resolve")
        for name, reason in failed_for.items():
            print(f"{name}: {reason}")


if __name__ == "__main__":
    main()
