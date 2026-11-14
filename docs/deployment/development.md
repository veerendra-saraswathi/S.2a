# Development and air-gap boundary

The JSON source launcher runs without network access or third-party package installation on Python 3.11+. Parquet/Iceberg requires the pinned storage dependency set; after installation its runtime is local-only. This demonstrates local execution; it is not a certified air-gapped appliance distribution.

Run from the repository root using `python3 tooling/s2.py`. The CLI defaults to `.local/s2.db`. Use separate databases for demo, integration tests and development. The demo creates a new synthetic tenant each time.

Production SaaS, dedicated VPC and air-gapped appliance directories are reserved. No cloud infrastructure, container image, customer deployment or GitHub publication was performed by the scaffold.

For a consistent reference backup, stop the development server and use SQLite's backup API; do not copy only the `.db` while ignoring a live WAL. Recovery objectives, encrypted backup automation and restore drills remain unqualified.

For file storage, back up the pinned warehouse together with the database while the application is stopped. Iceberg files contain absolute URIs; restore at the same location. Run `storage-report` after restore. See `../architecture/file-storage.md` for explicit limitations.
