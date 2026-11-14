# Third-party notices

The JSON reference backend has no third-party Python runtime dependencies beyond the Python standard library (including its SQLite binding). No third-party source or model weights are vendored here. Python and SQLite distribution licenses still apply when packaging their runtimes.

Setuptools is an optional build dependency for wheel creation and is not bundled. CI uses GitHub-maintained Actions. Release packaging must inventory all runtime, build, container and model dependencies and include their applicable notices. S2 ownership headers must never replace upstream licenses.

## Optional storage extra

v0.2 optionally depends on PyArrow (Apache Arrow), Apache PyIceberg and SQLAlchemy, with their transitive dependencies. Their licenses apply independently of S2's proprietary license. No third-party source, binaries or wheelhouse is bundled in the source ZIP. `requirements-storage.lock` records the versions tested on macOS/Python 3.13. `docs/operations/storage-dependencies.json` records installed package metadata; it is an inventory, not a signed or complete release SBOM.
