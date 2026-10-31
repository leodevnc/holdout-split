"""Local CLI. Reports contain counts and identifiers, never case text."""

import argparse
import json
import sys

from .core import InputError, build, extend, verify_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description="Keep related agent-evaluation cases in the same split.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("build", help="Create a new development/holdout bundle")
    create.add_argument("input")
    create.add_argument("--out", required=True)
    create.add_argument("--holdout-percent", type=int, default=25)
    create.add_argument("--seed", type=int, default=17)
    grow = sub.add_parser("extend", help="Add cases without moving or changing existing cases")
    grow.add_argument("input", help="Complete new dataset, including all previous cases")
    grow.add_argument("--base", required=True)
    grow.add_argument("--out", required=True)
    check = sub.add_parser("verify", help="Verify stored bytes, grouping, and assignments")
    check.add_argument("bundle")
    check.add_argument("--parent", help="Also verify an extension against its immediate parent")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build(args.input, args.out, holdout_percent=args.holdout_percent, seed=args.seed)
        elif args.command == "extend":
            result = extend(args.base, args.input, args.out)
        else:
            result = verify_bundle(args.bundle, parent=args.parent)
        counts = result["manifest"]["counts"]
        actual = round(100 * counts["holdout"] / sum(counts.values()), 2)
        target = result["manifest"]["policy"]["holdout_percent"]
        warnings = []
        missing_provenance = sum(not case["source_ids"] and case["conversation_id"] is None
                                 for case in result["cases"])
        if missing_provenance:
            warnings.append(f"{missing_provenance} cases have no provenance IDs; only normalized-input matching applies to them.")
        if actual != target:
            warnings.append("Actual ratio differs from the target; related groups and frozen assignments take precedence.")
        if result["manifest"]["mode"] == "extend" and not result["lineage_verified"]:
            warnings.append("Parent preservation was not checked. Supply --parent to verify immediate lineage.")
        print(json.dumps({"verified": True, "counts": counts, "groups": len(result["groups"]),
                          "cases_without_provenance": missing_provenance,
                          "actual_holdout_percent": actual, "target_holdout_percent": target,
                          "lineage_verified": result["lineage_verified"], "warnings": warnings}, sort_keys=True))
        return 0
    except (InputError, OSError) as error:
        # Filesystem errors may contain filenames but never echo dataset contents.
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
