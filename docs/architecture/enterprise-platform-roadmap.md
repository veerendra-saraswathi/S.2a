# Samanvaya enterprise platform roadmap

S.2a owns the shared data, ontology, policy and action services. S0.7 supplies the imaging vocabulary and laboratory evidence used in the first integration. The same platform should support a different engineering domain by changing its source bindings and object definitions.

| Part | Design principle | Application to Samanvaya |
| --- | --- | --- |
| 1. Platform architecture | Give applications and reviewers consistent definitions of the things they work with. | S.2a owns object identities, relationship types, permissions and action contracts. S0.7 defines what a codec run measures and what evidence an imaging reviewer needs. |
| 2. Data integration | Preserve the origin and version of a record as it moves from a source system into a shared model. | The imaging demo imports six baseline reports and a replay timing file through dataset transactions and bindings. It retains source paths, report digests and collection manifest identities. The next milestone is to reopen the project and repeat the import without duplicating runs. |
| 3. Ontology | Model the entities and relationships an engineer needs to answer a question. | Each CodecRun has typed links to a Collection, a CodecBuild and an EvidenceReport. Two replays can cite the same timing file while retaining separate run identities. |
| 4. Analytics | Use object links to find the right evidence and tabular measurements to investigate individual observations. | Query the six baseline runs for deadline misses, then open the cited report for frame-level timings. Count the 900 baseline observations separately from replay observations. |
| 5. Applications | Build the review interface against the same object identities, permissions and actions. | Proposed: selecting a run opens its collection, build and source report. An authorised reviewer can record a disposition against the selected run revision. |
| 6. Workflows | Attach review tasks and approvals to the records that prompted them. | Proposed: a deadline miss opens a review. The reviewer can attach a replay and record a conclusion while preserving the original measurements and their source. |
| 7. Enterprise AI | Give an assistant a bounded, permission-aware query and evaluate its answers against known evidence. | Proposed: ask which collections missed their deadlines. The assistant should cite the authorised reports, distinguish baseline runs from replays and reproduce the deterministic query result. |

## First demonstrated result

The local imaging demo uses a temporary S.2a project. Reading back the materialised objects and links yields **32 deadline misses across 900 baseline frames**. The original ePoikf run accounts for 17 misses. Its two replays each record zero misses in 150 frames. Keeping all three records lets a reviewer see how the result varied without losing the original observation.

See the [imaging evidence demo](../../tooling/imaging_evidence_demo.py) and its [integration tests](../../tests/test_imaging_evidence_demo.py).

## Next acceptance test

Import the same evidence twice into a persistent local project, close the process, and reopen it without access to the source checkout. The query should recover six baseline runs, two distinct replays and one shared codec build. The baseline total and evidence references should survive both the repeated import and the restart.
