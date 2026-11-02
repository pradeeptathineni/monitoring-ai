#!/usr/bin/env python3

# tools/compare.py - Compare inspected upstreams within a cohesive feature set.
# Usage: python3 tools/compare.py --category analytics --min-stars 5000
#

import argparse
import json
from pathlib import Path


def main():
    cli = argparse.ArgumentParser(description=__doc__ or "Filter the bounded upstream feature-set catalog")
    cli.add_argument("--category")
    cli.add_argument("--feature-set")
    cli.add_argument("--tag")
    cli.add_argument("--min-stars", type=int, default=0)
    cli.add_argument("--json", action="store_true")
    args = cli.parse_args()
    catalog = json.loads((Path(__file__).resolve().parents[1] / "docs/featuresets.json").read_text())
    sources = {source["repo"]: source for source in catalog["sources"]}
    matches = []
    for feature in catalog["feature_sets"]:
        if args.category and feature["category"] != args.category:
            continue
        if args.feature_set and feature["id"] != args.feature_set:
            continue
        if args.tag and args.tag not in feature["tags"]:
            continue
        candidates = sorted((sources[repo] for repo in feature["candidates"]
                             if sources[repo]["stars"] >= args.min_stars),
                            key=lambda source: source["stars"], reverse=True)
        if candidates:
            matches.append(dict(feature, sources=candidates))
    if args.json:
        print(json.dumps(matches, indent=2))
    else:
        for feature in matches:
            print(f"\n{feature['name']} [{feature['category']}]")
            print(feature["workflow"])
            for source in feature["sources"]:
                print(f"  {source['stars']:>6,}  {source['repo']}  {source['license']}  pushed {source['pushed_at'][:10]}")
            print(feature["decision"])
        print(f"\n{len(matches)} cohesive feature sets. Star counts observed {catalog['observed_at']}.")


if __name__ == "__main__":
    main()
