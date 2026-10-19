"""Set the episode-metadata file numbers of a merged dataset. Run by merge_datasets.sh.

Each row in meta/episodes/chunk-XXX/file-YYY.parquet has the columns `meta/episodes/chunk_index`
and `meta/episodes/file_index`, which name the file the row is stored in. LeRobot 0.6.1's merge
keeps the numbers of the source datasets, which do not match when a source has several
meta/episodes files (as datasets recorded in several sessions with record.py --resume have).
Training reads every file and does not use these columns, but dataset edits such as
delete_episodes do. This script sets both columns to the file each row is stored in.

Usage: python set_episode_file_indices.py <dataset folder>
"""

import re
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

root = Path(sys.argv[1])
files = sorted((root / "meta" / "episodes").glob("chunk-*/file-*.parquet"))
if not files:
    sys.exit(f"No episode metadata in {root}")

updated = 0
for path in files:
    chunk = int(re.fullmatch(r"chunk-(\d+)", path.parent.name).group(1))
    file = int(re.fullmatch(r"file-(\d+)\.parquet", path.name).group(1))
    table = pq.read_table(path)
    for column, value in (("meta/episodes/chunk_index", chunk), ("meta/episodes/file_index", file)):
        old = table.column(column)
        if any(v != value for v in old.to_pylist()):
            updated += 1
        new = pa.array([value] * table.num_rows, type=old.type)
        table = table.set_column(table.schema.get_field_index(column), column, new)
    pq.write_table(table, path)

print(f"Episode metadata: {len(files)} file(s) checked, {updated} column(s) updated.")
