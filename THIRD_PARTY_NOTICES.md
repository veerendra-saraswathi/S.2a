# Third-party notices

Gate 1 has no third-party Python runtime dependencies beyond the Python standard library (including its SQLite binding). No third-party source or model weights are vendored here. Python and SQLite distribution licenses still apply when packaging their runtimes.

Setuptools is an optional build dependency for wheel creation and is not bundled. CI uses GitHub-maintained Actions. Release packaging must inventory all runtime, build, container and model dependencies and include their applicable notices. S2 ownership headers must never replace upstream licenses.
