# Holdout Split

Turn agent-evaluation cases into reproducible development and holdout files without splitting related cases across the boundary.

This tool is for developers who maintain prompt, tool-selection, or structured-answer evaluation datasets. Supply a JSONL file with questions, expected outputs, and optional conversation/document identifiers. Receive two usable JSONL datasets, an explanation of their grouping, and a verifiable manifest. Add cases later without silently moving existing examples or changing their labels.

The tool performs real local file processing. It does not call a model, simulate an agent, score answers, or connect to a service. The included cases are synthetic examples; no model-quality claim is made.

## Run the complete example

Python 3.11+ is sufficient. No dependencies or credentials are needed:

```sh
git clone https://github.com/leodevnc/holdout-split.git
cd holdout-split
mkdir -p runs
python3 -m holdout_split build examples/cases.jsonl --out runs/v1
python3 -m holdout_split verify runs/v1
python3 -m holdout_split extend examples/extended.jsonl --base runs/v1 --out runs/v2
python3 -m holdout_split verify runs/v2 --parent runs/v1
```

The initial eight cases form five related groups and produce **6 development cases and 2 holdout cases**. The extension adds an unrelated case to development and a refund-policy case to the existing holdout group, producing **7 development cases and 3 holdout cases**. All original case assignments stay unchanged.

Inspect `runs/v1/dev.jsonl` when developing your agent. Keep `runs/v1/holdout.jsonl` for a separately controlled evaluation. The files retain the original input/expected content, including multilingual text. A small adapter may be needed for your evaluator's format; no evaluator integration is bundled.

Do not tune against the holdout after inspecting its results. A filesystem split cannot enforce that discipline or provide access control.

## What gets grouped?

Two cases are linked if they share any of:

- A non-null `conversation_id`.
- An entry in `source_ids`.
- An input after Unicode NFKC normalization, case folding, and whitespace collapsing.

Links are transitive. If A shares a conversation with B and B shares a document with C, all three stay together. Missing provenance does not link otherwise unrelated cases. The CLI warns when cases have no provenance identifiers.

Normalization is used only for matching. It does not rewrite questions, translate data, remove cases, or resolve inconsistent expected answers.

## Input format

One case per line:

```json
{"id":"refund-1","input":"Find the refund procedure.","expected":{"tool":"search_docs"},"conversation_id":"support-42","source_ids":["refund-policy"]}
```

| Field | Contract |
| --- | --- |
| `id` | Required unique identifier |
| `input` | Required nonblank string, at most 16,384 characters |
| `expected` | Required JSON value; carried through without interpreting or grading it |
| `conversation_id` | Optional identifier or null; defaults to null |
| `source_ids` | Optional array of up to 16 distinct identifiers; defaults to an empty array |

Identifiers are 1-128 ASCII letters, digits, dots, underscores, or hyphens and begin with a letter or digit. Unknown fields, duplicate JSON keys, duplicate case IDs, non-finite numbers, blank lines, and malformed UTF-8 are rejected. Expected-value nesting is capped at 20 levels. Each line/canonical case is at most 64 KiB; the full dataset is at most 32 MiB and 5,000 cases. Source IDs are sorted in the output; absent optional fields are materialized.

Use stable provenance IDs. Different IDs for the same underlying document prevent that relationship from being detected. An overly broad shared ID can collapse the whole dataset into one group; the tool refuses to split a single group.

## Build, verify, extend

```sh
python3 -m holdout_split build cases.jsonl --out runs/custom --holdout-percent 25 --seed 17
python3 -m holdout_split verify runs/custom
```

The requested percentage is a target, not a guarantee. Whole related groups take precedence. A deterministic greedy allocator keeps both outputs nonempty and reports the realized ratio. It is not an optimal subset solver or a label-stratified sampler. Input row order does not affect output bytes. The seed breaks ranking ties; it does not guarantee statistical independence.

`extend` takes a **complete new dataset**, including all old cases:

```sh
python3 -m holdout_split extend all-cases-v2.jsonl --base runs/custom --out runs/custom-v2
```

- Existing records and assignments must remain unchanged after canonicalization.
- New cases linked to an existing group inherit its split.
- New independent groups enter development, keeping unreviewed new material out of the holdout by default.
- A new direct or transitive connection between frozen development and holdout groups rejects the entire extension before output creation.

Try the rejection case:

```sh
python3 -m holdout_split extend examples/bridged.jsonl --base runs/v1 --out runs/rejected
```

This deliberately exits **2**: the new case connects a deployment document in development with a refund document in holdout. `runs/rejected` is not created. Correct the provenance if it is wrong, omit the new bridging case if appropriate, or explicitly create a new benchmark version with `build`. The tool never silently repairs this by moving old cases.

## Stored bundle

```text
v1/
  dev.jsonl
  holdout.jsonl
  groups.json
  manifest.json
```

`groups.json` lists members, assigned splits, and a spanning forest explaining the connections. It is not an exhaustive list of every matching field. `manifest.json` records policy, counts, file digests, a canonical dataset digest, and an immediate-parent manifest digest for extensions.

`verify` checks bytes, canonical records, group boundaries, and counts. For an initial build, it also reproduces the allocation. For an extension, **use `--parent` to check preservation against the immediate parent**. Without it, verification reports `lineage_verified: false` and a warning. Grandparent history is not recursively verified.

The output directory must be new and its parent must already exist. Existing paths are never overwritten. The manifest is written last as a completion marker. A failed write can leave an incomplete directory; verification rejects it. Inspect that directory and use a new output path to retry. There is no automatic cleanup, resume, or power-loss durability guarantee.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The suite invokes the real CLI in separate processes, builds and reloads artifacts, extends frozen splits, rejects contamination bridges, races two publishers, injects write failures, and checks a grouping implementation against an independent breadth-first-search reference on 100 seeded datasets. See [experiments](docs/experiments.md) for reproducible expectations.

Optional installation with `python3 -m pip install .` provides `holdout-split`. Installation may fetch build tooling; running from the checkout needs only Python. Offline packaging requires preinstalled setuptools and wheel-building support.

## Limits and data handling

- Detects declared provenance and normalized exact-input overlap, **not** semantic paraphrases, translated duplicates, temporal leakage, answer leakage, or model pretraining contamination.
- No model provider is instantiated. The boundary is the input/output dataset contract; model execution and scoring remain outside this tool. Test fixtures exercise actual processing, not fake model quality.
- `verified: true` means the declared structural checks passed, not that a benchmark is unbiased, representative, or independent in every sense.
- Holdout membership may grow through related additions. That preserves group boundaries but does not by itself make the added cases an untouched evaluation set.
- Digests detect mismatches, not malicious rewriting by someone who can update the whole bundle. They are not signatures.
- Bundles contain original input and expected-answer data. Keep real datasets private. CLI summaries do not print case contents, but filesystem errors may include paths.
- Direct symlink inputs/artifacts are rejected. This is a trusted-local-workspace tool, not a sandbox against hostile path races or ancestor-directory replacement.

See [architecture decisions](docs/architecture.md) and the [roadmap](docs/roadmap.md).
