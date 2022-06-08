import argparse
import yaml

import asyncio
import httpx


CONCURRENCY = 20


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("yarn_lock_file", type=str, help="path to Cargo.lock")

    args = parser.parse_args()

    with open(args.yarn_lock_file, "r") as stream:
        data = yaml.safe_load(stream)

    deps = {}
    for package, data in data.items():
        if package == "__metadata":
            continue
        if package.startswith("root-workspace"):
            continue
        if ", " in package:
            package = package.split(", ")[0]
        package, _, _ = package.rpartition("@")
        if "@patch:" in package:
            package, _, _ = package.rpartition("@patch:")
        if package.startswith("@edgedb/"):
            # our own packages
            continue
        deps[package] = data["version"]

    sem = asyncio.Semaphore(CONCURRENCY)

    failed_for = {}

    async def worker():
        async with httpx.AsyncClient() as client:
            while True:
                async with sem:
                    try:
                        package, ver = deps.popitem()
                    except KeyError:
                        return

                    url = f"https://registry.npmjs.com/{package}"
                    resp = await client.get(url)

                    data = resp.json()

                    try:
                        print(f"{package}: {data['license']}")
                    except KeyError:
                        failed_for[package] = "no license data on npmjs.com"

    await asyncio.gather(*[worker() for _ in range(CONCURRENCY)])

    if failed_for:
        print("\n\nFailed for:")
        for name, reason in failed_for.items():
            print(f"{name}: {reason}")


if __name__ == "__main__":
    asyncio.run(main())
