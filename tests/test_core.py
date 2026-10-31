import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from holdout_split.core import (
    FILES, MAX_LINE, InputError, _artifacts, assign, build, decode, digest,
    encode, extend, groups, normalized_input, parse_cases, policy,
    publish, read_bytes, validate_case, verify_bundle,
)


def case(case_id, text=None, *, conversation=None, sources=None):
    return validate_case({"id": case_id, "input": text or f"Question for {case_id}",
                          "expected": {"answer": case_id}, "conversation_id": conversation,
                          "source_ids": sources or []})


def write_cases(path, rows):
    path.write_bytes(b"".join(encode(row) for row in rows))


class ParsingTests(unittest.TestCase):
    def test_unicode_content_is_preserved(self):
        raw = {"id": "ko", "input": "  장애 대응 절차를 찾아줘.  ", "expected": "検索"}
        result = parse_cases(encode(raw))[0]
        self.assertEqual(result["input"], raw["input"])
        self.assertEqual(result["expected"], "検索")

    def test_normalization_only_changes_matching_identity(self):
        self.assertEqual(normalized_input(" ＡＢＣ\t Hello\n"), "abc hello")
        raw = case("a", " ＡＢＣ\t Hello\n")
        self.assertEqual(raw["input"], " ＡＢＣ\t Hello\n")

    def test_duplicate_id_and_blank_lines_rejected(self):
        a = encode(case("a"))
        for data in (a + a, a + b"\n", b"", b"\n"):
            with self.subTest(data=data), self.assertRaises(InputError):
                parse_cases(data)

    def test_malformed_json_rejected(self):
        for data in (b'{"id":"a","id":"b"}', b'{"n":NaN}', b'{"n":Infinity}', b'\xff', b'{', b'[' * 2000):
            with self.subTest(data=data[:30]), self.assertRaises(InputError):
                decode(data)

    def test_schema_rejections(self):
        base = case("a")
        invalid = [None, [], {}, dict(base, extra=True), dict(base, id="../x"),
                   dict(base, input=" "), dict(base, input=1), dict(base, input="a" * 16385),
                   dict(base, source_ids="source"), dict(base, source_ids=["x", "x"]),
                   dict(base, source_ids=[str(i) for i in range(17)]),
                   dict(base, conversation_id=1), dict(base, expected=float("nan")),
                   dict(base, input="\ud800")]
        for row in invalid:
            with self.subTest(row_type=type(row)), self.assertRaises(InputError):
                validate_case(row)

    def test_depth_and_line_caps(self):
        nested = 1
        for _ in range(22):
            nested = [nested]
        with self.assertRaises(InputError):
            validate_case(dict(case("a"), expected=nested))
        with self.assertRaises(InputError):
            parse_cases(b"x" * (MAX_LINE + 1))

    def test_policy_types_and_ranges(self):
        for percent, seed in ((0, 1), (100, 1), (True, 1), (25, True), (25, -1), (25, 2**32)):
            with self.subTest(percent=percent, seed=seed), self.assertRaises(InputError):
                policy(percent, seed)


class GroupTests(unittest.TestCase):
    def test_transitive_conversation_source_and_duplicate_links(self):
        rows = [case("a", conversation="thread"),
                case("b", conversation="thread", sources=["doc"]),
                case("c", "Same prompt", sources=["doc"]),
                case("d", " SAME  PROMPT "), case("e")]
        components = groups(rows)
        self.assertEqual([g["members"] for g in components], [["a", "b", "c", "d"], ["e"]])
        self.assertEqual({edge["reason"] for edge in components[0]["links"]}, {"conversation", "source", "input"})

    def test_source_and_conversation_namespaces_are_distinct(self):
        rows = [case("a", conversation="same"), case("b", sources=["same"])]
        self.assertEqual(len(groups(rows)), 2)

    def test_missing_provenance_does_not_link_every_record(self):
        self.assertEqual(len(groups([case("a"), case("b")])), 2)

    def test_single_component_is_not_silently_split(self):
        with self.assertRaises(InputError):
            assign(groups([case("a", sources=["shared"]), case("b", sources=["shared"])]), policy(50, 1))

    def test_both_splits_nonempty_even_for_tiny_target(self):
        components = groups([case("a"), case("b")])
        for percent in (1, 50, 99):
            with self.subTest(percent=percent):
                self.assertEqual(set(assign(components, policy(percent, 1)).values()), {"dev", "holdout"})

    def test_determinism_under_input_permutation(self):
        rows = [case(str(i), sources=[f"doc-{i // 2}"]) for i in range(20)]
        expected = groups(rows)
        assignments = assign(expected, policy(25, 17))
        for seed in range(20):
            shuffled = rows.copy()
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(groups(shuffled), expected)
            self.assertEqual(assign(groups(shuffled), policy(25, 17)), assignments)

    def test_graph_components_against_independent_breadth_first_search(self):
        for seed in range(100):
            rng = random.Random(seed)
            rows = [case(str(i), f"Prompt {rng.randrange(15)}", conversation=f"t{rng.randrange(20)}",
                         sources=[f"s{rng.randrange(20)}"]) for i in range(20)]
            adjacency = {row["id"]: set() for row in rows}
            for a in rows:
                for b in rows:
                    if (normalized_input(a["input"]) == normalized_input(b["input"])
                        or a["conversation_id"] == b["conversation_id"]
                        or set(a["source_ids"]) & set(b["source_ids"])):
                        adjacency[a["id"]].add(b["id"])
            unseen = set(adjacency)
            reference = set()
            while unseen:
                pending = [min(unseen)]
                found = set()
                while pending:
                    current = pending.pop()
                    if current not in found:
                        found.add(current)
                        pending.extend(adjacency[current] - found)
                unseen -= found
                reference.add(frozenset(found))
            self.assertEqual({frozenset(g["members"]) for g in groups(rows)}, reference)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input.jsonl"
        self.rows = [case(str(i), sources=[f"doc-{i}"]) for i in range(8)]
        write_cases(self.input, self.rows)
        self.base = self.root / "base"
        self.result = build(self.input, self.base)

    def new_input(self, rows):
        path = self.root / "new.jsonl"
        write_cases(path, rows)
        return path

    def test_bundle_counts_and_original_content(self):
        self.assertEqual(self.result["manifest"]["counts"], {"dev": 6, "holdout": 2})
        self.assertEqual(self.result["cases"], self.rows)
        self.assertEqual(verify_bundle(self.base)["manifest"], self.result["manifest"])

    def test_build_is_byte_reproducible(self):
        shuffled = list(reversed(self.rows))
        other = self.root / "other"
        build(self.new_input(shuffled), other)
        for name in (*FILES, "manifest.json"):
            self.assertEqual((self.base / name).read_bytes(), (other / name).read_bytes())

    def test_no_overwrite(self):
        before = (self.base / "manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            build(self.input, self.base)
        self.assertEqual((self.base / "manifest.json").read_bytes(), before)

    def test_invalid_input_creates_no_output(self):
        path = self.root / "bad.jsonl"
        path.write_bytes(b"not json")
        output = self.root / "bad-output"
        with self.assertRaises(InputError):
            build(path, output)
        self.assertFalse(output.exists())

    def test_extension_preserves_assignments_and_defaults_new_groups_to_dev(self):
        holdout_id = next(key for key, value in self.result["assignments"].items() if value == "holdout")
        additions = [case("new-unrelated"), case("new-related", sources=[f"doc-{holdout_id}"])]
        result = extend(self.base, self.new_input(self.rows + additions), self.root / "extended")
        self.assertTrue(result["lineage_verified"])
        for key, value in self.result["assignments"].items():
            self.assertEqual(result["assignments"][key], value)
        self.assertEqual(result["assignments"]["new-unrelated"], "dev")
        self.assertEqual(result["assignments"]["new-related"], "holdout")

    def test_bridge_is_rejected_before_output_creation(self):
        representatives = {split: next(key for key, value in self.result["assignments"].items() if value == split)
                           for split in ("dev", "holdout")}
        addition = case("bridge", sources=[f"doc-{key}" for key in representatives.values()])
        output = self.root / "rejected"
        with self.assertRaisesRegex(InputError, "bridge"):
            extend(self.base, self.new_input(self.rows + [addition]), output)
        self.assertFalse(output.exists())

    def test_edit_or_removal_of_existing_case_is_rejected(self):
        changed = [dict(row) for row in self.rows]
        changed[0]["expected"] = {"changed": True}
        for index, rows in enumerate((changed, self.rows[1:])):
            with self.subTest(index=index), self.assertRaisesRegex(InputError, "retain"):
                extend(self.base, self.new_input(rows), self.root / f"rejected-{index}")

    def test_case_change_bool_vs_integer_is_rejected(self):
        self.rows[0]["expected"] = True
        path = self.new_input(self.rows)
        other = self.root / "boolean-base"
        build(path, other)
        self.rows[0]["expected"] = 1
        with self.assertRaisesRegex(InputError, "retain"):
            extend(other, self.new_input(self.rows), self.root / "bad-bool")

    def test_parent_required_to_claim_lineage(self):
        output = self.root / "extended"
        extend(self.base, self.new_input(self.rows + [case("new")]), output)
        self.assertFalse(verify_bundle(output)["lineage_verified"])
        self.assertTrue(verify_bundle(output, parent=self.base)["lineage_verified"])
        with self.assertRaisesRegex(InputError, "parent"):
            verify_bundle(output, parent=output)

    def test_corruption_and_missing_files_are_rejected(self):
        file = self.base / "dev.jsonl"
        original = file.read_bytes()
        file.write_bytes(original + b" ")
        with self.assertRaisesRegex(InputError, "digest"):
            verify_bundle(self.base)
        file.unlink()
        with self.assertRaisesRegex(InputError, "incomplete"):
            verify_bundle(self.base)

    def test_unexpected_files_are_rejected(self):
        (self.base / "extra.txt").write_text("unexpected")
        with self.assertRaisesRegex(InputError, "unexpected"):
            verify_bundle(self.base)

    def test_rehashed_cross_split_leakage_is_still_rejected(self):
        holdout_id = next(key for key, value in self.result["assignments"].items() if value == "holdout")
        dev = parse_cases((self.base / "dev.jsonl").read_bytes())
        dev[0]["source_ids"] = [f"doc-{holdout_id}"]
        payload = b"".join(encode(row) for row in dev)
        (self.base / "dev.jsonl").write_bytes(payload)
        manifest = self.result["manifest"]
        manifest["files"]["dev.jsonl"] = digest(payload)
        (self.base / "manifest.json").write_bytes(encode(manifest))
        with self.assertRaisesRegex(InputError, "cross"):
            verify_bundle(self.base)

    def test_incomplete_publication_is_not_accepted(self):
        output = self.root / "partial"
        original_open = Path.open

        def interrupted(path, *args, **kwargs):
            if path.parent == output and path.name == "holdout.jsonl":
                raise OSError("injected disk failure")
            return original_open(path, *args, **kwargs)

        data, manifest = _artifacts(self.result["cases"], self.result["groups"],
                                    self.result["assignments"], self.result["manifest"]["policy"])
        with patch.object(Path, "open", interrupted), self.assertRaises(OSError):
            publish(output, data, manifest)
        self.assertTrue(output.exists())
        self.assertFalse((output / "manifest.json").exists())
        with self.assertRaisesRegex(InputError, "incomplete"):
            verify_bundle(output)
        with self.assertRaises(FileExistsError):
            publish(output, data, manifest)

    def test_symlink_inputs_and_artifacts_are_rejected(self):
        link = self.root / "link.jsonl"
        link.symlink_to(self.input)
        with self.assertRaises(InputError):
            read_bytes(link)
        artifact = self.base / "groups.json"
        copy = self.root / "groups-copy.json"
        copy.write_bytes(artifact.read_bytes())
        artifact.unlink()
        artifact.symlink_to(copy)
        with self.assertRaises(InputError):
            verify_bundle(self.base)

    def test_false_manifest_counts_are_rejected(self):
        manifest = self.result["manifest"]
        manifest["counts"]["dev"] = 100
        (self.base / "manifest.json").write_bytes(encode(manifest))
        with self.assertRaisesRegex(InputError, "counts"):
            verify_bundle(self.base)

    def test_canonical_growth_is_rejected_before_publication(self):
        minimal = [{"id": str(i), "input": str(i), "expected": 1} for i in range(4)]
        path = self.new_input(minimal)
        output = self.root / "oversize-canonical"
        with patch("holdout_split.core.MAX_BYTES", len(path.read_bytes()) + 5):
            with self.assertRaisesRegex(InputError, "canonical"):
                build(path, output)
        self.assertFalse(output.exists())

    def test_extension_chain_preserves_the_immediate_parent(self):
        second = self.root / "v2"
        rows = self.rows + [case("new-a")]
        extend(self.base, self.new_input(rows), second)
        third = self.root / "v3"
        final = extend(second, self.new_input(rows + [case("new-b")]), third)
        self.assertTrue(final["lineage_verified"])
        self.assertTrue(verify_bundle(third, parent=second)["lineage_verified"])
        with self.assertRaisesRegex(InputError, "parent"):
            verify_bundle(third, parent=self.base)

    def test_transitive_bridge_through_multiple_new_cases_is_rejected(self):
        dev_id = next(key for key, value in self.result["assignments"].items() if value == "dev")
        holdout_id = next(key for key, value in self.result["assignments"].items() if value == "holdout")
        additions = [case("link-a", conversation="new-thread", sources=[f"doc-{dev_id}"]),
                     case("link-b", conversation="new-thread", sources=[f"doc-{holdout_id}"])]
        with self.assertRaisesRegex(InputError, "bridge"):
            extend(self.base, self.new_input(self.rows + additions), self.root / "bridge-chain")


if __name__ == "__main__":
    unittest.main()
