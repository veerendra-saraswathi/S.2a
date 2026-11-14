# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Immutable local Parquet/Iceberg artifacts; the S2 manifest is the publication boundary.

Iceberg uses an isolated, temporary catalog to construct one sealed table per S2
snapshot. Reads pin metadata and snapshot IDs, never a mutable catalog head.
"""
import hashlib
import json
import os
import re
from pathlib import Path
from s2_platform import S2Error, canonical, digest, new_id


class StorageError(S2Error):
    status = 503


def file_hash(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class FileStorage:
    def __init__(self, root, backend):
        if backend not in {'parquet', 'iceberg'}:
            raise S2Error('Unknown file storage backend')
        self.root = Path(root).expanduser().resolve()
        self.backend = backend
        self._dependencies(backend)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _dependencies(backend):
        try:
            import pyarrow  # noqa: F401
            if backend == 'iceberg':
                import pyiceberg  # noqa: F401
                import sqlalchemy  # noqa: F401
        except ImportError as exc:
            raise StorageError('File storage dependencies missing; install the S2 storage extra') from exc

    def _checkpoint(self, phase):
        """Fault-injection seam used only by tests; never controlled by API inputs."""

    def _path(self, relative):
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise StorageError('Invalid storage reference')
        parts = Path(relative).parts
        if any(part in {'.', '..'} for part in parts):
            raise StorageError('Unsafe storage reference')
        path = self.root.joinpath(*parts)
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise StorageError('Symlinks are not storage objects')
        if not path.resolve().is_relative_to(self.root):
            raise StorageError('Storage reference escapes warehouse')
        return path

    @staticmethod
    def _arrow(payload):
        import pyarrow as pa
        # Physical names cannot collide with metadata fields or unusual source names.
        mapping = {name: 'c' + str(index) for index, name in enumerate(sorted(payload['schema']['properties']))}
        types = {'string': pa.string(), 'integer': pa.int64(), 'number': pa.float64(), 'boolean': pa.bool_()}
        fields = [pa.field(mapping[name], types[spec['type']], nullable=True)
                  for name, spec in sorted(payload['schema']['properties'].items())]
        fields += [pa.field('_s2_row', pa.string(), nullable=False), pa.field('_s2_ordinal', pa.int64(), nullable=False)]
        schema = pa.schema(fields, metadata={b's2.column_mapping': canonical(mapping).encode()})
        records = []
        for ordinal, row in enumerate(payload['rows']):
            record = {'_s2_row': canonical(row), '_s2_ordinal': ordinal}
            for name, value in row['values'].items():
                spec = payload['schema']['properties'][name]
                if spec['type'] == 'number' and type(value) is int and abs(value) > 2**53:
                    raise S2Error('Integer-valued number exceeds exact float64 range; use integer type')
                if spec['type'] == 'integer' and not -(2**63) <= value < 2**63:
                    raise S2Error('File-backed integer properties require signed 64-bit values')
                record[mapping[name]] = value
            records.append(record)
        # The canonical envelope preserves absent optional fields, numeric representation
        # and Markings exactly; typed columns support external format inspection.
        return pa.Table.from_pylist(records, schema=schema), mapping

    def write(self, tenant, payload):
        if not re.fullmatch(r'[0-9a-f-]{36}', tenant):
            raise StorageError('Invalid tenant storage namespace')
        artifact = tenant + '/' + new_id()
        directory = self._path(artifact)
        directory.mkdir(parents=True, mode=0o700)
        try:
            table, mapping = self._arrow(payload)
            self._checkpoint('allocated')
            if self.backend == 'parquet':
                import pyarrow.parquet as pq
                entry = directory / 'data.parquet'
                pq.write_table(table, entry, compression='zstd')
                snapshot_id = None
            else:
                from pyiceberg.catalog.sql import SqlCatalog
                # Ephemeral staging catalog. Its head is not an S2 serving interface.
                catalog = SqlCatalog('s2_build', uri='sqlite:///:memory:', warehouse=directory.as_uri())
                try:
                    catalog.create_namespace('staging')
                    iceberg = catalog.create_table(('staging', 'snapshot'), schema=table.schema,
                        location=directory.as_uri(), properties={'format-version': '2', 'write.parquet.compression-codec': 'zstd'})
                    if table.num_rows:
                        iceberg.append(table)
                    from urllib.parse import urlparse, unquote
                    entry = Path(unquote(urlparse(iceberg.metadata_location).path))
                    snapshot = iceberg.current_snapshot()
                    snapshot_id = snapshot.snapshot_id if snapshot else None
                finally:
                    catalog.engine.dispose()
            self._checkpoint('data_written')
            files = []
            for file in sorted(directory.rglob('*')):
                if file.is_file():
                    with file.open('rb') as stream:
                        os.fsync(stream.fileno())
                    files.append({'path': file.relative_to(directory).as_posix(),
                                  'sha256': file_hash(file), 'bytes': file.stat().st_size})
            seal = {'format': self.backend, 'tenant': tenant, 'artifact': artifact,
                    'entry': entry.relative_to(directory).as_posix(), 'snapshot_id': snapshot_id,
                    'row_count': len(payload['rows']), 'rows_sha256': digest(payload['rows']),
                    'columns': mapping, 'files': files}
            seal_file = directory / 'SEALED.json'
            with seal_file.open('x', encoding='utf-8') as stream:
                stream.write(canonical(seal))
                stream.flush()
                os.fsync(stream.fileno())
            directories = [p for p in directory.rglob('*') if p.is_dir()]
            for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                sync_directory(path)
            for path in (directory, directory.parent, self.root, self.root.parent):
                sync_directory(path)
            descriptor = {'version': 1, 'format': self.backend, 'artifact': artifact,
                          'seal_sha256': file_hash(seal_file)}
            # Verify both file integrity and a real reader round-trip before publication.
            restored = self.read(tenant, descriptor)
            if canonical(restored) != canonical(payload['rows']):
                raise StorageError('Storage round-trip changed dataset values')
            self._checkpoint('sealed')
            return descriptor
        except S2Error:
            raise
        except Exception as exc:
            # Keep incomplete artifacts for recovery inspection; no catalog pointer exists.
            raise StorageError('Dataset file preparation failed; branch was not published') from exc

    def verify(self, tenant, descriptor):
        try:
            if descriptor.get('version') != 1 or descriptor.get('format') not in {'parquet', 'iceberg'}:
                raise StorageError('Unsupported storage descriptor')
            artifact = descriptor['artifact']
            if len(Path(artifact).parts) != 2 or Path(artifact).parts[0] != tenant:
                raise StorageError('Cross-tenant storage reference rejected')
            directory = self._path(artifact)
            seal_file = self._path(artifact + '/SEALED.json')
            if file_hash(seal_file) != descriptor['seal_sha256']:
                raise StorageError('Snapshot seal checksum mismatch')
            seal = json.loads(seal_file.read_text())
            if (seal['tenant'] != tenant or seal['artifact'] != artifact or
                    seal['format'] != descriptor['format']):
                raise StorageError('Snapshot identity mismatch')
            declared = set()
            for item in seal['files']:
                relative = item['path']
                if relative in declared or Path(relative).is_absolute():
                    raise StorageError('Invalid sealed file list')
                declared.add(relative)
                path = self._path(artifact + '/' + relative)
                if not path.is_relative_to(directory):
                    raise StorageError('Sealed file escapes artifact')
                if path.stat().st_size != item['bytes'] or file_hash(path) != item['sha256']:
                    raise StorageError('Snapshot file checksum mismatch')
            if seal['entry'] not in declared:
                raise StorageError('Snapshot entry is not sealed')
            return seal
        except StorageError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StorageError('Snapshot files missing, unreadable or invalid') from exc

    def read(self, tenant, descriptor):
        seal = self.verify(tenant, descriptor)
        entry = self._path(descriptor['artifact'] + '/' + seal['entry'])
        self._dependencies(seal['format'])
        try:
            if seal['format'] == 'parquet':
                import pyarrow.parquet as pq
                arrow = pq.ParquetFile(entry).read()
            else:
                from pyiceberg.table import StaticTable
                static = StaticTable.from_metadata(entry.as_uri(), properties={'py-io-impl': 'pyiceberg.io.pyarrow.PyArrowFileIO'})
                snapshot = static.current_snapshot()
                if (snapshot.snapshot_id if snapshot else None) != seal['snapshot_id']:
                    raise StorageError('Pinned Iceberg snapshot mismatch')
                arrow = static.scan(snapshot_id=seal['snapshot_id']).to_arrow()
            records = sorted(arrow.to_pylist(), key=lambda record: record['_s2_ordinal'])
            if [record['_s2_ordinal'] for record in records] != list(range(len(records))):
                raise StorageError('Invalid dataset row ordinals')
            rows = [json.loads(record['_s2_row']) for record in records]
            if len(rows) != seal['row_count'] or digest(rows) != seal['rows_sha256']:
                raise StorageError('Dataset row integrity mismatch')
            return rows
        except S2Error:
            raise
        except Exception as exc:
            raise StorageError('Pinned dataset snapshot could not be read') from exc

    def report(self, references):
        """Host-only, read-only inventory. Caller holds the catalog writer lock."""
        known, broken = set(), []
        for tenant, revision, descriptor in references:
            known.add(descriptor['artifact'])
            try:
                self.read(tenant, descriptor)
            except StorageError as exc:
                broken.append({'revision': revision, 'error': str(exc)})
        unreferenced = []
        for tenant in sorted(self.root.iterdir()):
            if tenant.is_dir() and not tenant.is_symlink():
                for path in sorted(tenant.iterdir()):
                    if path.is_dir() and not path.is_symlink():
                        relative = path.relative_to(self.root).as_posix()
                        if relative not in known:
                            unreferenced.append({'artifact': relative, 'sealed': (path / 'SEALED.json').is_file()})
        return {'referenced_artifacts': len(known), 'broken_references': broken,
                'unreferenced_artifacts': unreferenced, 'deleted': 0}
