# Hosted backups and restore checks

`tools/hosted_backup.py` packages the private PostgreSQL application schema and every cover referenced by an event, including private or cancelled events. It does not copy unused bucket objects, Supabase Auth, other schemas, environment files, passwords, storage keys, or hosting settings. Keep the deployment configuration separately in a private password manager. The current application uses Google through Flask; its accounts are in the application schema.

This is an operator-run backup, not an automatic backup schedule. A private off-site destination, retention schedule, and failure alerts still need configuring. The ZIP contains student and payment-related records. Store it outside the repository, never attach it to a group chat, and use encrypted storage. File permissions restrict local access; the ZIP itself is not encrypted.

## Prepare the operator's computer

Use the application's Python environment and PostgreSQL client tools compatible with the source server. Check `pg_dump --version`; an older major client cannot dump a newer server. Commands below run from `event-hub/`.

Create a private named connection service, `broncoshub`, in `~/.pg_service.conf` (or a private file selected by `PGSERVICEFILE`). Use the existing session pooler, restricted application username and database name from the deployment settings. Set `sslmode=verify-full` and `sslrootcert` to the absolute path of `certs/supabase-ca.crt`. The tool also enforces verified TLS. Keep the password in a `0600` PostgreSQL password file, selected by `PGPASSFILE` if needed. Do not put a connection URL or password in a command argument or shell history.

Load `SUPABASE_URL`, `SUPABASE_SECRET_KEY` and `SUPABASE_STORAGE_BUCKET=event-covers` into the operator process from the existing private secret store. The tool reads those settings from the environment; it does not print them, load a repository `.env`, or include them in the archive. It uses the existing bounded private Storage reader, refuses redirects, and does not write to Supabase.

## Make and check a backup

1. Stop application writes, the payment recovery worker, notification workers, and any other database/storage editors. Suspend the web service or use a deliberately implemented maintenance procedure; idle sleep is not a maintenance lock. Do not create a backup while students are changing events or covers. The flag below is an operator assertion, not a mechanism that stops the app.
2. Run the backup with a new private filename:

   ```sh
   .venv/bin/python tools/hosted_backup.py backup \
     --service broncoshub --schema broncoshub \
     --output /private/backup/location/broncoshub-YYYY-MM-DD.zip --app-stopped
   ```

3. Restart the app/workers after the command succeeds or fails. A failed command does not publish an incomplete archive. Keep any known-good earlier backup.
4. Verify the archive and copy it to the chosen private off-site destination:

   ```sh
   .venv/bin/python tools/hosted_backup.py verify /private/backup/location/broncoshub-YYYY-MM-DD.zip
   ```

The tool exports a repeatable-read database snapshot shared with `pg_dump`. It records table counts and referenced cover names, streams a bounded dump, downloads those cover files, and records SHA-256 hashes. The final `0600` archive appears atomically only after inventory, sizes and hashes pass. Existing files are never overwritten. The current bounds are 512 MB for the database dump, 5 MB per cover, 10,000 referenced covers and 2 GB total. If the project grows beyond them, review the strategy rather than silently skipping files.

Archive verification proves file integrity, not successful database restoration or backup authenticity. Only restore backups created by the trusted team: a PostgreSQL dump can contain executable SQL, and a forged manifest can carry matching hashes.

## Restore drill on a separate local database

The tool deliberately has no command that restores over a live database. Use a separate local PostgreSQL server and a **new empty database** named `broncoshub_restore_<unique-name>`. Keep the cloud database and production Storage settings out of that server's configuration.

1. Extract to a new private folder:

   ```sh
   .venv/bin/python tools/hosted_backup.py extract \
     /private/backup/location/broncoshub-YYYY-MM-DD.zip \
     /private/restore-check-YYYY-MM-DD
   ```

2. Create a separate `broncoshub-restore-local` service profile with local host, port and local operator credentials. Choose a new database name, create it locally, and restore the trusted dump into that database:

   ```sh
   createdb --maintenance-db='service=broncoshub-restore-local host=127.0.0.1 hostaddr=127.0.0.1 dbname=postgres' broncoshub_restore_YYYYMMDD
   pg_restore --dbname='service=broncoshub-restore-local host=127.0.0.1 hostaddr=127.0.0.1 dbname=broncoshub_restore_YYYYMMDD' \
     --no-owner --no-privileges --exit-on-error --single-transaction \
     /private/restore-check-YYYY-MM-DD/database.dump
   ```

   Replace `YYYYMMDD` with lowercase letters/digits or the actual numeric date. `createdb` must succeed before `pg_restore`; never continue if the database already exists. Run these as separate commands. Do not use `--clean` or point either command at the live server. Do not restore untrusted archives. The temporary local database is not a production deployment; its owner/security configuration is deliberately local.

3. Compare the restored table counts and cover references with the verified archive:

   ```sh
   .venv/bin/python tools/hosted_backup.py check-restored \
     /private/backup/location/broncoshub-YYYY-MM-DD.zip \
     --service broncoshub-restore-local --database broncoshub_restore_YYYYMMDD
   ```

   This command forces a loopback connection and a read-only transaction. It only accepts the scratch database prefix. It checks table counts and cover references; it does not prove every application behavior. Review representative restored accounts/events/registrations and open cover files from the extracted folder. If starting the app against the scratch database, keep Google, payments and notifications disabled, select local cover storage pointing at the extracted `covers` folder, and use the private schema as the search path.

4. Record when the drill passed and who checked it. Delete only the named scratch database/folder when finished according to the team's retention decision. The tool does not delete them automatically.

Before recovering actual production, rebuild private schema permissions/RLS and private bucket access using the deployment guide, review the data cutoff, and reconcile external Stripe transactions. Restoring a database does not rewind charges, payouts, refunds or already sent messages. Keep paid checkout and outbound notifications disabled until reconciliation is complete.

References checked November 6, 2026: [Supabase backups](https://supabase.com/docs/guides/platform/backups), [PostgreSQL connection services](https://www.postgresql.org/docs/current/libpq-pgservice.html), [pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html). The Supabase changelog's current CLI-v3 and TLS notices were reviewed; this tool uses PostgreSQL clients and the application's current HTTPS Storage reader, not the Supabase CLI.
