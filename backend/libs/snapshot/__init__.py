"""A dataset version as an immutable Parquet file (DF-04, F-01; docs/blueprint/DATA_PIPELINE_SPEC.md §1).

`writer.write` copies a version's table out of PostgreSQL into Parquet (zstd, page checksums,
provenance in the file's metadata), names it by its sha256 and puts it once in the locked bucket
da-snapshots; `reader.query` runs SQL on snapshots in DuckDB with the column policies of whoever
reads applied, so an answer and its receipt read their numbers again once the version's table is
gone (DF-06). Cataloguing a snapshot and its event are the caller's (DF-05).

SNAPSHOT_ENABLED=0 writes none: `writer.write` answers skipped. The package imports nothing: worker-ingest
writes (pyarrow) and has no DuckDB, so a caller imports `libs.snapshot.writer` or `.reader`.
"""
