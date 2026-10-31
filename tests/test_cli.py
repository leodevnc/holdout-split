import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def invoke(self, *arguments, expected=0):
        process = subprocess.run([sys.executable, "-m", "holdout_split", *map(str, arguments)],
                                 cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, expected, process.stderr)
        self.assertNotIn("Traceback", process.stderr)
        return json.loads(process.stdout if expected == 0 else process.stderr)

    def test_actual_cli_build_restart_verify_and_extend(self):
        source = ROOT / "examples" / "cases.jsonl"
        output = self.root / "v1"
        first = self.invoke("build", source, "--out", output)
        self.assertTrue(first["verified"])
        self.assertEqual(first["counts"], {"dev": 6, "holdout": 2})
        self.assertEqual(first, self.invoke("verify", output))
        new_data = self.root / "new.jsonl"
        addition = {"id": "new-case", "input": "Check a distinct local artifact.", "expected": {"tool": "read_file"}}
        new_data.write_bytes(source.read_bytes() + (json.dumps(addition) + "\n").encode())
        second = self.invoke("extend", new_data, "--base", output, "--out", self.root / "v2")
        self.assertEqual(second["counts"], {"dev": 7, "holdout": 2})
        self.assertTrue(second["lineage_verified"])
        verified = self.invoke("verify", self.root / "v2", "--parent", output)
        self.assertEqual(verified, second)
        standalone = self.invoke("verify", self.root / "v2")
        self.assertFalse(standalone["lineage_verified"])
        self.assertTrue(any("Parent" in warning for warning in standalone["warnings"]))

    def test_existing_output_and_bad_inputs_exit_two(self):
        output = self.root / "v1"
        self.invoke("build", ROOT / "examples" / "cases.jsonl", "--out", output)
        self.assertIn("error", self.invoke("build", ROOT / "examples" / "cases.jsonl", "--out", output, expected=2))
        source = self.root / "bad.jsonl"
        source.write_text('{"input":"PRIVATE_EXAMPLE_TEXT"}\n')
        response = self.invoke("build", source, "--out", self.root / "bad", expected=2)
        self.assertNotIn("PRIVATE_EXAMPLE_TEXT", json.dumps(response))
        self.assertFalse((self.root / "bad").exists())

    def test_concurrent_builders_cannot_overwrite_each_other(self):
        output = self.root / "shared"
        args = [sys.executable, "-m", "holdout_split", "build", str(ROOT / "examples" / "cases.jsonl"), "--out", str(output)]
        processes = [subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for process in processes:
            process.communicate(timeout=20)
        self.assertEqual(sorted(process.returncode for process in processes), [0, 2])
        self.assertTrue(self.invoke("verify", output)["verified"])

    def test_checked_in_extension_and_rejected_bridge(self):
        base = self.root / "base"
        self.invoke("build", ROOT / "examples/cases.jsonl", "--out", base)
        result = self.invoke("extend", ROOT / "examples/extended.jsonl", "--base", base, "--out", self.root / "extended")
        self.assertEqual(result["counts"], {"dev": 7, "holdout": 3})
        error = self.invoke("extend", ROOT / "examples/bridged.jsonl", "--base", base, "--out", self.root / "rejected", expected=2)
        self.assertIn("bridge", error["error"])
        self.assertFalse((self.root / "rejected").exists())


if __name__ == "__main__":
    unittest.main()
