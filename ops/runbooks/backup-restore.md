# Backup & Restore Runbook

## Overview

The Atlas free/self-hosted deployment includes four backup-related scripts in
`deploy/free/`. This runbook documents the operational procedures, RPO/RTO
targets, and restore drill process.

## Scripts

| Script | Purpose | Default |
|--------|---------|---------|
| `deploy/free/backup-postgres.sh` | `pg_dump` + gzip, timestamped | Writes to `backups/atlas-<stamp>.sql.gz` |
| `deploy/free/restore-postgres.sh` | Restore from a `.sql.gz` file | Requires `ATLAS_RESTORE_CONFIRM=1` |
| `deploy/free/check-latest-backup.sh` | Assert backup freshness (for Prometheus/Dead Man's Snitch) | Max age: 48h |
| `deploy/free/prune-backups.sh` | Delete backups older than retention window | Retention: 14 days |

## Key Metrics

| Metric | Target | Notes |
|--------|--------|-------|
| **RPO** (Recovery Point Objective) | ≤ 24 hours | Full `pg_dump` daily is acceptable for MVP |
| **RTO** (Recovery Time Objective) | ≤ 2 hours | Full restore from `.sql.gz` on equivalent hardware |
| **Backup frequency** | Daily | Cron: `0 2 * * *` |
| **Retention** | 14 days | Adjustable via `BACKUP_RETENTION_DAYS` |
| **Monitoring** | Backup freshness checked every 5 min | Prometheus Blackbox or `check-latest-backup.sh` in cron |

## Procedures

### Daily backup (cron)

```
0 2 * * * /opt/atlas/deploy/free/backup-postgres.sh >> /var/log/atlas-backup.log 2>&1
```

Backups go to `deploy/free/backups/atlas-<stamp>.sql.gz`.

### Weekly backup verification

Run the restore against a **throwaway database** to verify the archive is valid:

```bash
# Create a disposable restore target
cd deploy/free
docker compose exec -T db psql -U postgres -c "CREATE DATABASE atlas_restore_drill;"

# Restore into the drill database
ATLAS_RESTORE_CONFIRM=1 \
  pg_restore_args="--dbname=postgresql://postgres:***@localhost:5432/atlas_restore_drill" \
  ./restore-postgres.sh backups/atlas-20260605T020000Z.sql.gz

# Smoke check: count tables, rows, or run a known query
docker compose exec -T db psql -U postgres -d atlas_restore_drill -c "\dt" | head -20

# Tear down
docker compose exec -T db psql -U postgres -c "DROP DATABASE atlas_restore_drill;"
```

### Production restore

1. **Stop the API** to prevent writes during restore:
   ```bash
   systemctl stop atlas-api
   # or: docker compose --profile full stop api
   ```

2. **Confirm the backup file** is the one you need:
   ```bash
   ls -lh deploy/free/backups/
   ```

3. **Run restore with confirmation**:
   ```bash
   ATLAS_RESTORE_CONFIRM=1 ./deploy/free/restore-postgres.sh backups/atlas-20260605T020000Z.sql.gz
   ```

4. **Verify data integrity**:
   ```bash
   docker compose exec -T db psql -U postgres -d atlas -c "SELECT count(*) FROM accident_events;"
   docker compose exec -T db psql -U postgres -d atlas -c "SELECT count(*) FROM claims;"
   ```

5. **Restart the API**:
   ```bash
   systemctl start atlas-api
   ```

6. **Verify application health**:
   ```bash
   curl -f http://localhost:8000/health
   curl -f http://localhost:8000/ready
   ```

### Backup freshness alerting

Run `check-latest-backup.sh` via cron every 5 minutes:

```
*/5 * * * * /opt/atlas/deploy/free/check-latest-backup.sh >> /var/log/atlas-backup-check.log 2>&1 || (echo "Backup stale" | mail -s "ALERT: Atlas backup" ops@example.com)
```

For Prometheus users, expose the check as a textfile collector:

```bash
#!/bin/bash
if deploy/free/check-latest-backup.sh 2>/dev/null; then
  echo "atlas_backup_fresh 1" > /var/lib/node_exporter/textfile/atlas_backup.prom
else
  echo "atlas_backup_fresh 0" > /var/lib/node_exporter/textfile/atlas_backup.prom
fi
```

### Retention pruning

Run `prune-backups.sh` after each successful backup to delete aged-out files:

```
30 2 * * * /opt/atlas/deploy/free/prune-backups.sh >> /var/log/atlas-backup-prune.log 2>&1
```

Set `BACKUP_RETENTION_DAYS=30` to keep a longer history.

## Off-Site Backup

MVP: `rsync` or `aws s3 cp` the `backups/` directory to an off-site location after
each successful backup:

```bash
aws s3 sync deploy/free/backups/ s3://atlas-backup-prod/ --storage-class STANDARD_IA
```

For S3, enable Object Lock or versioning to protect against accidental deletion.
For rsync, use a pull model from the off-site host so a compromised API server
cannot erase the backup repository.

## Disaster Recovery

| Scenario | Recovery | RTO |
|----------|----------|-----|
| Accidental table drop | Point-in-time restore from backup | ≤ 2h |
| Database corruption | Restore latest clean backup | ≤ 2h |
| Full instance loss | Spin up new host + restore latest backup | ≤ 4h |
| Region-level outage | Cross-region restore from off-site backup | ≤ 8h |

For scenarios requiring point-in-time recovery (PITR), configure PostgreSQL WAL
archiving separately (`archive_mode=on` + `archive_command`). This runbook covers
full-database restore only.
