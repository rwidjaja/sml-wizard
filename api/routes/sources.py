"""GET /api/sources, GET /api/sources/{id}/schemas — source picker + catalogue tree.

Everything here goes through AtScale's own metadata REST API (see
atscale/client.py) so Databricks catalog/schema/table, Snowflake
database/schema/table, and Postgres database/schema/table all work through one
code path with no warehouse credentials in Flask.

Confirmed against a real instance (docker-atscale.atscaledomain.com):
- The `conn/{connId}` path segment is the data-warehouse's `connectionId` field
  (e.g. "PostgresDB"), not the warehouse's own `id` UUID or an inner
  `connections[].id` - both of those 404/500.
- `/databases` and `/databases/{db}/schemas` return plain lists of strings.
- `/schemas/{schema}/tables` returns a plain list of table-name strings (not
  objects) - `.../tables/{table}/info` is where columns actually live.
- A warehouse can have >1 database (Databricks catalogs show up as multiple
  "databases" here), so the wizard's data-source picklist is one entry per
  (connectionId, database) pair, matching the design doc's
  "Postgres — atscale_tutorial_data" style label.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from routes.session import get_client

sources_bp = Blueprint("sources", __name__)

# Schemas AtScale/Postgres/Databricks create for their own bookkeeping - never
# real user data, so hide them from the catalogue tree.
_SYSTEM_SCHEMA_PATTERNS = (
    "information_schema",
    "pg_catalog",
    "pg_toast",
    "pg_temp",
    "atscale_aggr",
)

_CACHE_TTL_SECONDS = 600
_schema_cache: dict[str, tuple[float, list]] = {}


def _is_system_schema(name: str, database: str) -> bool:
    lname = name.lower()
    if lname == database.lower():
        return True
    return any(lname.startswith(p) for p in _SYSTEM_SCHEMA_PATTERNS)


@sources_bp.get("/sources")
def list_sources():
    client = get_client()
    if not client:
        return jsonify({"error": "Not authenticated"}), 401

    warehouses = client.list_data_sources()
    out = []
    for w in warehouses:
        connection_id = w.get("connectionId")
        if not connection_id:
            continue
        try:
            databases = client.list_databases(connection_id)
        except Exception:
            continue
        for database in databases:
            out.append(
                {
                    "id": f"{connection_id}::{database}",
                    "label": f"{w.get('name')} — {database}",
                    "dialect": w.get("platformType"),
                    "connectionId": connection_id,
                    "database": database,
                }
            )
    return jsonify(out)


def _split_source_id(source_id: str) -> tuple[str, str]:
    connection_id, _, database = source_id.partition("::")
    return connection_id, database


def _load_all_schemas(client, connection_id: str, database: str) -> list[dict]:
    """Fetches every schema/table/column for a source, unfiltered. Cached (by
    source id only - search is applied in-memory below, never part of the
    cache key, so a search request can't return a stale unrelated result)."""
    schema_names = [s for s in client.list_schemas(connection_id, database) if not _is_system_schema(s, database)]

    # One round-trip per table for column info (AtScale has no bulk endpoint) -
    # sequential took ~30s over ~120 tables on the dev instance. Fan these out
    # with a thread pool since they're pure I/O waits, not CPU work.
    schema_tables: list[tuple[str, list[str]]] = [
        (schema_name, client.list_tables(connection_id, database, schema_name)) for schema_name in schema_names
    ]

    def fetch(schema_name: str, table_name: str) -> tuple[str, str, dict]:
        info = client.get_table_info(connection_id, database, schema_name, table_name)
        return schema_name, table_name, info

    jobs = [(schema_name, table_name) for schema_name, table_names in schema_tables for table_name in table_names]
    infos: dict[tuple[str, str], dict] = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=16) as pool:
            for schema_name, table_name, info in pool.map(lambda job: fetch(*job), jobs):
                infos[(schema_name, table_name)] = info

    result = []
    for schema_name, table_names in schema_tables:
        tables = []
        for table_name in table_names:
            info = infos.get((schema_name, table_name)) or {}
            tables.append(
                {
                    "name": table_name,
                    "columns": [
                        {"name": c.get("name"), "type": c.get("dataType")}
                        for c in info.get("columns", [])
                    ],
                }
            )
        result.append({"name": schema_name, "tables": tables})
    return result


@sources_bp.get("/sources/<path:source_id>/schemas")
def list_schemas(source_id: str):
    client = get_client()
    if not client:
        return jsonify({"error": "Not authenticated"}), 401

    connection_id, database = _split_source_id(source_id)
    if not connection_id or not database:
        return jsonify({"error": f"Malformed source id '{source_id}'"}), 400

    cached = _schema_cache.get(source_id)
    if cached and time.time() - cached[0] < _CACHE_TTL_SECONDS:
        all_schemas = cached[1]
    else:
        all_schemas = _load_all_schemas(client, connection_id, database)
        _schema_cache[source_id] = (time.time(), all_schemas)

    search = request.args.get("search", "").lower()
    if not search:
        return jsonify(all_schemas)

    filtered = []
    for schema in all_schemas:
        tables = [t for t in schema["tables"] if search in t["name"].lower()]
        if tables:
            filtered.append({"name": schema["name"], "tables": tables})
    return jsonify(filtered)
