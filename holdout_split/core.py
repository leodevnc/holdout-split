"""Group related cases, preserve frozen assignments, and verify local bundles."""

import hashlib
import json
import re
import unicodedata
from pathlib import Path


MAX_BYTES = 32 * 1024 * 1024
MAX_LINE = 64 * 1024
MAX_CASES = 5000
NORMALIZATION = "nfkc-casefold-whitespace-v1"
SPLITS = ("dev", "holdout")
FILES = ("dev.jsonl", "holdout.jsonl", "groups.json")


class InputError(ValueError):
    pass


def encode(value) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise InputError("value cannot be represented as bounded UTF-8 JSON") from error


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON object key")
        result[key] = value
    return result


def _constant(_):
    raise InputError("non-finite JSON numbers are not allowed")


def decode(data: bytes):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise InputError("invalid UTF-8 JSON (duplicate keys and non-finite numbers are rejected)") from error


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise InputError("identifiers must be 1-128 ASCII letters, digits, dots, underscores, or hyphens")
    return value


def _depth(value, level=0):
    if level > 20:
        raise InputError("JSON nesting exceeds 20 levels")
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)


def validate_case(raw):
    required = {"id", "input", "expected"}
    allowed = required | {"conversation_id", "source_ids"}
    if not isinstance(raw, dict) or not required <= raw.keys() or raw.keys() - allowed:
        raise InputError("case fields must be id, input, expected, and optional conversation_id/source_ids")
    case_id = _identifier(raw["id"])
    text = raw["input"]
    if not isinstance(text, str) or not text.strip() or len(text) > 16384:
        raise InputError("input must contain 1-16384 characters and cannot be whitespace-only")
    conversation = raw.get("conversation_id")
    if conversation is not None:
        _identifier(conversation)
    sources = raw.get("source_ids", [])
    if not isinstance(sources, list) or len(sources) > 16:
        raise InputError("source_ids must be an array with at most 16 identifiers")
    for source in sources:
        _identifier(source)
    if len(set(sources)) != len(sources):
        raise InputError("source_ids cannot contain duplicates")
    _depth(raw["expected"])
    result = {"id": case_id, "input": text, "expected": raw["expected"],
              "conversation_id": conversation, "source_ids": sorted(sources)}
    if len(encode(result)) > MAX_LINE:
        raise InputError("canonical case exceeds 64 KiB")
    return result


def read_bytes(path: Path, limit=MAX_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise InputError("input must be an existing regular file, not a symlink")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise InputError("file exceeds the size limit")
    return data


def parse_cases(data: bytes, *, allow_empty=False) -> list[dict]:
    if len(data) > MAX_BYTES:
        raise InputError("dataset exceeds 32 MiB")
    cases = []
    ids = set()
    for number, line in enumerate(data.splitlines(), 1):
        if not line.strip() or len(line) > MAX_LINE:
            raise InputError(f"line {number}: blank or exceeds 64 KiB")
        try:
            case = validate_case(decode(line))
        except InputError as error:
            raise InputError(f"line {number}: {error}") from error
        if case["id"] in ids:
            raise InputError(f"line {number}: duplicate case identifier")
        ids.add(case["id"])
        cases.append(case)
        if len(cases) > MAX_CASES:
            raise InputError("dataset exceeds 5000 cases")
    if not cases and not allow_empty:
        raise InputError("dataset cannot be empty")
    return sorted(cases, key=lambda case: case["id"])


def normalized_input(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def groups(cases: list[dict]) -> list[dict]:
    """Return connected components and one explaining spanning forest."""
    parents = {case["id"]: case["id"] for case in cases}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    owners = {}
    edges = []
    for case in sorted(cases, key=lambda item: item["id"]):
        features = [("input", digest(normalized_input(case["input"]).encode("utf-8")))]
        if case["conversation_id"] is not None:
            features.append(("conversation", case["conversation_id"]))
        features.extend(("source", source) for source in case["source_ids"])
        for feature in features:
            previous = owners.setdefault(feature, case["id"])
            a, b = root(previous), root(case["id"])
            if a != b:
                parents[max(a, b)] = min(a, b)
                edges.append({"left": previous, "right": case["id"], "reason": feature[0]})
    components = {}
    for case in cases:
        components.setdefault(root(case["id"]), []).append(case["id"])
    forests = {key: [] for key in components}
    for edge in edges:
        forests[root(edge["left"])].append(edge)
    return [
        {"id": digest(encode(sorted(members))), "members": sorted(members), "links": forests[key]}
        for key, members in sorted(components.items())
    ]


def policy(holdout_percent, seed):
    if type(holdout_percent) is not int or not 1 <= holdout_percent <= 99:
        raise InputError("holdout percent must be an integer from 1 to 99")
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise InputError("seed must be an integer from 0 to 4294967295")
    return {"holdout_percent": holdout_percent, "seed": seed, "normalization": NORMALIZATION}


def assign(components, settings):
    if len(components) < 2:
        raise InputError("all cases form one related group; a nonempty independent holdout is impossible")
    total = sum(len(group["members"]) for group in components)
    target = total * settings["holdout_percent"]
    ranked = sorted(components, key=lambda group: (
        abs(len(group["members"]) * 100 - target),
        digest(encode([settings["seed"], group["id"]]))))
    selected = set()
    count = 0
    for group in ranked:
        size = len(group["members"])
        if count + size < total and abs((count + size) * 100 - target) < abs(count * 100 - target):
            selected.add(group["id"])
            count += size
    if not selected:
        selected.add(min(ranked, key=lambda group: abs(len(group["members"]) * 100 - target))["id"])
    return {member: "holdout" if group["id"] in selected else "dev"
            for group in components for member in group["members"]}


def _jsonl(cases):
    return b"".join(encode(case) for case in sorted(cases, key=lambda case: case["id"]))


def _artifacts(cases, components, assignments, settings, parent=None):
    canonical_dataset = _jsonl(cases)
    if len(canonical_dataset) > MAX_BYTES:
        raise InputError("canonical dataset exceeds 32 MiB")
    annotated = [dict(group, split=assignments[group["members"][0]]) for group in components]
    data = {f"{split}.jsonl": _jsonl([case for case in cases if assignments[case["id"]] == split])
            for split in SPLITS}
    data["groups.json"] = encode(annotated)
    manifest = {
        "format_version": 1, "mode": "extend" if parent else "build", "policy": settings,
        "parent_manifest_sha256": parent, "dataset_sha256": digest(canonical_dataset),
        "counts": {split: sum(value == split for value in assignments.values()) for split in SPLITS},
        "files": {name: digest(value) for name, value in data.items()},
    }
    return data, manifest


def publish(output, data, manifest):
    output = Path(output)
    # mkdir is the exclusive reservation. Never overwrite an existing bundle.
    output.mkdir(mode=0o700)
    for name, value in data.items():
        with (output / name).open("xb") as stream:
            stream.write(value)
    # This is a completion marker, not a power-loss-safe atomic directory commit.
    with (output / "manifest.json").open("xb") as stream:
        stream.write(encode(manifest))


def _check_parent(cases, assignments, parent):
    indexed = {case["id"]: case for case in cases}
    for case in parent["cases"]:
        if case["id"] not in indexed or encode(indexed[case["id"]]) != encode(case):
            raise InputError("extension must retain every existing case without changing its content or provenance")
        if assignments[case["id"]] != parent["assignments"][case["id"]]:
            raise InputError("extension cannot move existing cases between splits")


def verify_bundle(directory, *, parent=None):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise InputError("bundle must be an existing directory, not a symlink")
    if {path.name for path in directory.iterdir()} != set(FILES) | {"manifest.json"}:
        raise InputError("bundle is incomplete or contains unexpected files")
    manifest = decode(read_bytes(directory / "manifest.json"))
    required = {"format_version", "mode", "policy", "parent_manifest_sha256", "dataset_sha256", "counts", "files"}
    if not isinstance(manifest, dict) or manifest.keys() != required or type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
        raise InputError("unsupported bundle manifest")
    settings = manifest["policy"]
    if not isinstance(settings, dict) or settings.keys() != {"holdout_percent", "seed", "normalization"}:
        raise InputError("invalid split policy")
    if settings != policy(settings["holdout_percent"], settings["seed"]):
        raise InputError("unsupported normalization policy")
    if manifest["mode"] not in ("build", "extend"):
        raise InputError("invalid bundle mode")
    parent_hash = manifest["parent_manifest_sha256"]
    if (manifest["mode"] == "build" and parent_hash is not None) or (manifest["mode"] == "extend" and (not isinstance(parent_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", parent_hash))):
        raise InputError("invalid parent manifest reference")
    if not isinstance(manifest["files"], dict) or manifest["files"].keys() != set(FILES):
        raise InputError("invalid artifact manifest")
    data = {name: read_bytes(directory / name) for name in FILES}
    if any(manifest["files"][name] != digest(data[name]) for name in FILES):
        raise InputError("artifact digest mismatch")
    cases = []
    assignments = {}
    for split in SPLITS:
        rows = parse_cases(data[f"{split}.jsonl"])
        for case in rows:
            if case["id"] in assignments:
                raise InputError("case identifier appears in both splits")
            assignments[case["id"]] = split
        cases.extend(rows)
    if len(cases) > MAX_CASES or len(_jsonl(cases)) > MAX_BYTES:
        raise InputError("combined bundle dataset exceeds the size limit")
    cases.sort(key=lambda case: case["id"])
    components = groups(cases)
    for group in components:
        if len({assignments[member] for member in group["members"]}) != 1:
            raise InputError("related cases cross the development/holdout boundary")
    if manifest["mode"] == "build" and assignments != assign(components, settings):
        raise InputError("assignments do not match the recorded build policy")
    expected_data, expected_manifest = _artifacts(cases, components, assignments, settings, parent_hash)
    if encode(manifest) != encode(expected_manifest) or data != expected_data:
        raise InputError("bundle contents do not match canonical records, groups, or counts")
    lineage_verified = False
    if parent is not None:
        previous = verify_bundle(parent)
        if parent_hash != digest(encode(previous["manifest"])):
            raise InputError("parent manifest mismatch")
        if settings != previous["manifest"]["policy"]:
            raise InputError("extension policy must match its parent")
        _check_parent(cases, assignments, previous)
        expected_assignments = _extended_assignments(cases, components, previous)
        if assignments != expected_assignments:
            raise InputError("extension assignments do not follow the frozen-parent policy")
        lineage_verified = True
    return {"manifest": manifest, "cases": cases, "assignments": assignments,
            "groups": components, "lineage_verified": lineage_verified}


def _extended_assignments(cases, components, previous):
    assignments = {}
    for group in components:
        pinned = {previous["assignments"][member] for member in group["members"]
                  if member in previous["assignments"]}
        if len(pinned) > 1:
            raise InputError("new cases bridge frozen dev and holdout groups; extension refused")
        split = next(iter(pinned)) if pinned else "dev"
        assignments.update({member: split for member in group["members"]})
    _check_parent(cases, assignments, previous)
    return assignments


def build(input_path, output, *, holdout_percent=25, seed=17):
    cases = parse_cases(read_bytes(Path(input_path)))
    settings = policy(holdout_percent, seed)
    components = groups(cases)
    assignments = assign(components, settings)
    data, manifest = _artifacts(cases, components, assignments, settings)
    publish(output, data, manifest)
    return verify_bundle(output)


def extend(base, input_path, output):
    previous = verify_bundle(base)
    cases = parse_cases(read_bytes(Path(input_path)))
    components = groups(cases)
    assignments = _extended_assignments(cases, components, previous)
    data, manifest = _artifacts(cases, components, assignments, previous["manifest"]["policy"],
                                digest(encode(previous["manifest"])))
    publish(output, data, manifest)
    return verify_bundle(output, parent=base)
