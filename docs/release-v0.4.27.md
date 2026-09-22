# AI Memory 0.4.27

- Free redundant local snapshots and raw backups after iCloud confirms that the
  immutable, previously synchronized object is uploaded. Missing confirmation,
  upload errors, size mismatches and active migrations retain the local copy.
- Compare resident iCloud copies byte for byte. Evicted iCloud objects use the
  successful destination-specific sync checkpoint and native upload/size metadata;
  retention does not download them again. The cloud object and current searchable
  conversation are not deleted by this retention pass.
- Reclaim verified snapshots before downloading the remaining history, so a long
  or interrupted synchronization does not defer all space reclamation. Raw copies
  are reclaimed after dependency validation completes.
- Do not recreate an already-synchronized current snapshot on every sync cycle.
- Maintain a transactional conversation-to-FTS-row lookup, populated once for
  existing indexes, to avoid rescanning the old large FTS content on every update.
- Run local index compaction even when cloud sync is paused or awaiting a retry,
  while retaining the existing import/sync locking and integrity checks.

No conversation content or raw cloud history is intentionally discarded. Local
historical/raw copies reclaimed by retention remain recoverable from iCloud;
current conversations remain locally searchable. Space reclamation is progressive
and depends on iCloud upload confirmation and completion of index migration.
