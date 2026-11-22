# Changelog

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
