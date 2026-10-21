-- One line per table of the `public` schema: its name and its row count, in name order.
-- Run it against a database and against its restore and diff the two outputs (a backup is a
-- day behind production, so for a restore from last night the counts are the night's):
--
--   psql -U <user> -d <database> -At -f row-counts.sql
--
-- `query_to_xml` counts each table from one generated query, so no table has to be named
-- here and a table added by a migration is compared without anyone editing this file.
SELECT table_name || '|' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;
