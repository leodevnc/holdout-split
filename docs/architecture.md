# Architecture and decisions

## Processing boundary

The application accepts local JSONL, validates and canonicalizes cases, constructs a relationship graph, allocates whole connected components, and writes a bundle. There are no network clients, executable configuration files, model adapters, or background services.

The `expected` value is opaque. It can hold a tool name, structured decision, or reference answer, but does not influence grouping or allocation. This avoids coupling dataset preparation to a particular model or evaluator. It also means conflicting labels are not resolved automatically.

## Relationship graph

Each case is a vertex. Typed features represent a normalized input digest, a conversation ID, or a source ID. An owner index connects a new case to an earlier case with the same feature. Union-find builds connected components without materializing every pairwise edge.

Source and conversation IDs have separate namespaces. Empty provenance produces no feature. Every successful union records an explaining edge; the resulting spanning forest is deterministic for sorted case IDs. It explains why a component is connected but omits redundant links.

Normalization is NFKC, case folding, and whitespace collapsing. This is intentionally conservative: case-sensitive code or identifiers can be merged even when their meanings differ. False merges reduce available independent groups; they do not split a known match across the boundary. Paraphrases and translated duplicates remain undetected.

## Allocation policy

For a target percentage `p` and total case count `n`, compare count errors as integers against `n * p`, avoiding floating-point allocation decisions.

1. Rank components by how close their individual size is to the requested holdout size. Break ties with a SHA-256 digest of the seed and component ID.
2. Add a component to holdout only when it strictly improves the current count error and leaves at least one case in development.
3. If no component was selected, choose the closest ranked component so the holdout is nonempty.
4. Put all remaining components in development.

At least two components are required. The allocator is a reproducible heuristic, not an optimizer or a statistical sampling guarantee. It does not stratify by difficulty, tool, language, or outcome. Changing the dataset can change a fresh build's assignments; use `extend` when preservation matters.

## Frozen extensions

An extension first verifies its parent, then rebuilds the relationship graph over the complete new dataset. Every old case must exist with identical canonical content. Each component collects its members' previous assignments:

| Prior assignments present | Result |
| --- | --- |
| None | Assign development |
| Development only | Assign development |
| Holdout only | Assign holdout |
| Both | Reject the extension |

This catches a bridge made from several new records, not just a single record carrying two sources. Ratio drift is reported rather than corrected by moving frozen cases. An unchanged dataset can be extended, but that creates no new evidence; users should avoid unnecessary versions.

The immediate-parent digest binds an extension to a canonical parent manifest. `verify --parent` checks existing records and assignments as well as the extension policy. It verifies that immediate relationship, not the entire ancestry or the authenticity of the dataset.

## Publication and failure behavior

All semantic input checks run before output creation. An exclusive `mkdir` reserves the destination, so two cooperating publishers cannot replace each other's output. Files are opened exclusively; the manifest is written last. Canonical JSON makes identical inputs and policy produce identical bytes without timestamps or machine paths.

A write failure may leave partial output. The verifier requires exactly the four expected regular files, validates digests, rebuilds groups, and compares canonical artifacts. Incomplete or altered bundles fail closed. Retrying into the same path is refused, leaving the evidence available for inspection.

This is not atomic directory publication, fsync-based crash durability, or a defense against a hostile concurrent filesystem writer. Storage is ordinary files because the workflow publishes immutable small datasets rather than operating a mutable service. An output directory is requested with mode `0700`; effective protection still depends on the host filesystem and access controls.

## Verification boundaries

File digests alone are insufficient: a mistaken writer could change both data and hashes. Verification also reconstructs components and checks that no component crosses the split. Initial builds must reproduce allocation from the stored policy. Extensions need their parent supplied to prove preservation.

None of these checks establish semantic independence, correct labels, representative sampling, or model quality. Provenance is caller-supplied. Users control whether holdout results feed back into development; this tool has no authorization or audit service to prevent that feedback.
