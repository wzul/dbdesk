# Changelog

## 0.6.1

- ClickHouse: optional HTTP path and encrypted custom HTTP headers (Cloudflare Access service tokens, API gateway headers).

## 0.6.0

- Schema tab is a structure grid (columns with charset, collation, nullable, default, extra, FK, comment; index grid) instead of a DDL block; DDL moved to a button.
- Multi-row filter panel with per-row enable checkbox, Apply, Apply all and disabled-filter skipping in WHERE.
- SQL toolbar: Save / Saved query buttons.
- Backup & restore endpoints for connections + saved views + saved queries (optional encrypted passwords).
- Preference storage for notifications and appearance.

## 0.5.0

- **Multi-database AI agent**: `/ai/catalog` + `/ai/agent`; the agent inspects schemas on demand, plans one read-only statement per database (max 3) and a SQLite join.
- Result sets are joined locally in in-memory SQLite; only schema, SQL, row counts and errors reach the provider.
- AI panel scope bar (all / current / none), per-source SQL blocks, local-join block, merged result and step log.
- Per-connection **AI can see this schema** and **AI can run read-only queries** switches; inspect-only mode.
- Agent reads (`ai_read`) go to the audit log; no production notification for reads.

## 0.4.0

- AI setup assistant on the picker: describe a database and get a field-by-field draft; secrets are stripped server-side.
- Settings (⌘,): updates, native notifications + test, AI provider, data path.

## 0.3.5

- Conversational replies without SQL.

## 0.3.4

- Cursor-style AI chat panel: thread, thought line, SQL block, composer.

## 0.3.3

- AI chat with history (multi-turn), read-only SQL.

## 0.2.0

- **SQLite** connections: open a local `.db` file (path instead of host/port), read-only via `mode=ro`.
- SQLite schema tree (`sqlite_master`), typed columns (`PRAGMA table_info`), indexes, DDL, inline edit/insert/delete, filters, CSV import/export, add/drop/rename column, index create/drop.
- SQLite statements run one at a time inside a transaction (stdlib `sqlite3`); BLOBs render as UTF-8 text or hex.
- Connection form: SQLite hides host/port/user/password/SSL/SSH and asks for a **Database file**.
- Activity drawer shows a SQLite-specific note (no server sessions).

## 0.1.0

First public release.

- Connections: PostgreSQL, MySQL/MariaDB, ClickHouse, MCP query tools, HTTP query gateways.
- SSH tunnel and custom tunnel command (SSM, cloudflared, bastion scripts) with `tunnel_env`.
- Encrypted credentials (AES-GCM) and 0600 connection store.
- Schema tree, DB switcher, per-database labels with WordPress auto-detect.
- Data grid: tabs, typed inline editors, insert/delete, row filters (case-insensitive contains), saved views, column show/hide, row detail, virtualized rows, Load more, copy/multi-select.
- SQL: autocomplete, formatter, history, saved queries, CSV, charts.
- Schema: DDL, cross-connection diff, add/drop/rename column, index editor.
- CSV import and whole-table export.
- Serialized-PHP parse/fix; WordPress Site tab.
- Production approvals (Telegram notice + Approvals), audit log, read-only guard.
- Activity monitor.
- Agent tools `db_connections`, `db_query` (read-only).
