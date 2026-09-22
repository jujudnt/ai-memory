# AI Memory 0.4.28

Follow-up to the storage fixes in 0.4.27, based on a real upgrade with several
gigabytes of local historical snapshots.

- Verify and index an existing local snapshot using its content-addressed SHA-256
  identity instead of downloading an identical object again. If the local copy
  fails verification, fetch and verify its replacement without discarding the
  original before a valid replacement is available.
- Reclaim previously synchronized, natively confirmed iCloud raw copies before
  starting a long history download, not only after the entire synchronization.
  Unconfirmed backups and migrations remain protected. Raw dependencies needed
  for validation are fetched on demand and retained until the current sync ends.

These changes do not delete cloud history or searchable local conversations.
