# s2-compute-pipelines

**Status:** Gate 2 planned.

Pipeline IR, visual DAG compilation, pinned build inputs, execution adapters and Data Health.

The Python package in this subsystem, where present, is the executable Gate 1 implementation. The boundary directories below reserve the approved decomposition; their README files do not represent implemented services.

- `pipeline-ir/`
- `dag-compiler/`
- `scheduler/`
- `execution-adapters/`
- `transform-registry/`
- `code-workspaces/`
- `data-health/`
- `pipeline-builder-ui/`

Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved — Proprietary Commercial Software.

## Executable reference slice (v0.5.0)

`s2_compute_pipelines/pipelines.py` implements explicit single-input projection/filter transforms, preview hashes, count/range checks and atomic replacement snapshots. See [contract](../../docs/architecture/versioned-pipelines.md). Remaining directories reserve future DAG, scheduling and worker responsibilities.
