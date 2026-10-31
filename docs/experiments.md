# Reproducible experiments

These experiments operate on the actual file-processing code. The example questions and expected tools are synthetic; no model is run or scored.

## A complete dataset lifecycle

From the checkout, select fresh output paths:

```sh
mkdir -p runs
python3 -m holdout_split build examples/cases.jsonl --out runs/example-v1
python3 -m holdout_split verify runs/example-v1
python3 -m holdout_split extend examples/extended.jsonl --base runs/example-v1 --out runs/example-v2
python3 -m holdout_split verify runs/example-v2 --parent runs/example-v1
```

Expected outcomes with the default seed 17 and target 25%:

| Operation | Development | Holdout | Groups | Immediate lineage checked |
| --- | ---: | ---: | ---: | --- |
| Initial build | 6 | 2 | 5 | Not applicable |
| Extension | 7 | 3 | 6 | Yes |

The three deployment cases stay together through a conversation/source chain. The two refund questions stay together because normalized input text matches even though one has no source ID. The Korean question remains unchanged. The extension adds one independent case to development and one refund-source case to holdout.

The extension's actual holdout ratio is 30%, not 25%. It reports that drift. Preserving established boundaries takes precedence over targeting a count.

## Refuse a new contamination bridge

```sh
python3 -m holdout_split extend examples/bridged.jsonl --base runs/example-v1 --out runs/example-rejected
```

Expect exit 2, a bridge error on stderr, and no output directory. The new case names both `deploy-policy` and `refund-policy`, joining two frozen splits. Arbitrarily assigning just the new record to one split would not repair the relationship graph.

## Verify the verifier

```sh
python3 -m unittest discover -s tests -v
```

The suite checks:

- Transitive grouping against an independent breadth-first-search implementation across 100 seeded datasets.
- Byte-identical output after input row permutations.
- Real CLI execution in separate processes for build, restart/reload, extension, and parent verification.
- A pair of simultaneous CLI publishers: exactly one succeeds, and the resulting bundle verifies.
- Duplicate identifiers, ambiguous JSON keys, malformed UTF-8, invalid policies, oversized records, and excessive nesting.
- Input/expected content and multilingual text preservation.
- Source/holdout bridges, including indirect bridges through two new records.
- Old-case removal and label changes, including JSON `true` versus `1`.
- Corrupted files, rehashed cross-split contamination, false counts, missing artifacts, and unexpected files.
- An injected disk-write failure: no manifest is produced, the partial directory fails verification, and a retry cannot overwrite it.

These tests support the stated local invariants. They do not measure model accuracy, dataset representativeness, filesystem power-loss recovery, or distributed performance.

## Observations

Checking only direct duplicate pairs is insufficient when provenance forms a chain. A component is the smallest safe allocation unit under the declared relationship policy.

A requested ratio is not a correctness requirement. Forcing a precise percentage can split a related group; reporting the actual percentage makes that tradeoff visible.

An incremental dataset is not merely a larger input file. New records can reveal relationships between previously separate groups. Refusing that bridge is more informative than quietly moving frozen cases.

Structural separation is only one part of evaluation hygiene. Unrecorded provenance, paraphrases, label leakage, and repeated feedback from holdout results remain outside this implementation.
