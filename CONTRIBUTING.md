# Development rules

This repository is proprietary. Contributions and redistribution require the owner's authorization; see LICENSE.

Keep domain ownership in the ten approved subsystems. Shared platform code contains contracts and primitives. Do not bypass the service authorization boundary, query another tenant's resources, write another subsystem's tables from an integration, or publish a branch head before its resources are valid.

Run `python3 -m unittest discover -s tests -v`, `python3 tooling/check_headers.py` and the synthetic demo before proposing changes. Add failure and authorization tests whenever transaction or access semantics change. Do not commit database files, tokens, proprietary customer data or model weights.

Document scope honestly: placeholders are planned; passing single-node tests is not proof of production scale or vendor parity. Preserve upstream licensing when adding third-party dependencies.
