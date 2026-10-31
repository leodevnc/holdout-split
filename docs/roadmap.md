# Scope and open directions

## Implemented

- Strict JSONL input with opaque expected outputs and optional provenance.
- Connected grouping through normalized input, conversation IDs, and source IDs.
- Reproducible development/holdout allocation and explanatory group records.
- Stored manifests and independent reload verification.
- Frozen extensions that reject removed/edited cases and cross-split bridges.
- Whole-workflow subprocess tests and failure-injection checks.

## Possible extensions

- An audit command for preexisting third-party splits, without reallocating them.
- Explicit time-based holdouts with documented treatment of groups that span a cutoff.
- Slice-coverage reports that expose underrepresented tools/languages without quietly changing frozen assignments.
- Configurable near-duplicate candidate review, keeping heuristic suggestions separate from confirmed provenance links.
- Dataset adapters for particular evaluation runners, preserving original expected values and case identity.

## Out of scope

Model invocation, grading, training, hosted storage, private-data discovery, semantic leakage guarantees, access control, and automatic resampling based on observed holdout scores.
