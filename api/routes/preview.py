"""GET /api/preview/catalogs, GET /api/preview/metadata, POST /api/preview/query

Cube data preview - browse a deployed catalog/cube's dimensions/hierarchies/
levels/measures and run an ad-hoc MDX or SQL query against it. Every call goes
through the same session-scoped AtScaleClient as the Build tab (routes/session.py's
get_client()) - see atscale/preview.py's module docstring for what's ported from
the user's reference tool vs. what reuses this app's own auth.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from atscale.preview import list_catalogs_and_cubes, load_cube_metadata, run_preview_query
from routes.session import get_client

preview_bp = Blueprint("preview", __name__)

# Cube metadata rarely changes mid-session and MDSCHEMA_LEVELS is one of four
# XMLA round-trips per (catalog, cube) - cache the whole load_cube_metadata()
# result, including the raw `_levels` list build_initial_mdx needs.
_metadata_cache: dict[tuple[str, str], dict] = {}


@preview_bp.get("/preview/catalogs")
def catalogs():
    client = get_client()
    if not client:
        return jsonify({"error": "Not authenticated"}), 401
    try:
        return jsonify(list_catalogs_and_cubes(client))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@preview_bp.get("/preview/metadata")
def metadata():
    client = get_client()
    if not client:
        return jsonify({"error": "Not authenticated"}), 401

    catalog = request.args.get("catalog")
    cube = request.args.get("cube")
    if not catalog or not cube:
        return jsonify({"error": "Missing 'catalog' or 'cube' query param"}), 400

    cache_key = (catalog, cube)
    cached = _metadata_cache.get(cache_key)
    if cached is not None:
        result = cached
    else:
        try:
            result = load_cube_metadata(client, catalog, cube)
        except Exception as e:
            return jsonify({"error": str(e)}), 502
        _metadata_cache[cache_key] = result

    return jsonify({"dimensions": result["dimensions"], "measures": result["measures"]})


@preview_bp.post("/preview/query")
def query():
    client = get_client()
    if not client:
        return jsonify({"error": "Not authenticated"}), 401

    body = request.get_json(force=True, silent=True) or {}
    catalog = body.get("catalog")
    cube = body.get("cube")
    dialect = body.get("dialect", "mdx")
    hierarchies = body.get("hierarchies") or []
    measures = body.get("measures") or []

    if not catalog or not cube:
        return jsonify({"error": "Missing 'catalog' or 'cube'"}), 400
    if not measures:
        return jsonify({"error": "Select at least one measure"}), 400
    if dialect == "mdx" and not hierarchies:
        return jsonify({"error": "Select at least one dimension/hierarchy for an MDX query"}), 400

    cache_key = (catalog, cube)
    cached = _metadata_cache.get(cache_key)
    if cached is None:
        try:
            cached = load_cube_metadata(client, catalog, cube)
        except Exception as e:
            return jsonify({"error": str(e)}), 502
        _metadata_cache[cache_key] = cached

    try:
        result = run_preview_query(
            client,
            catalog,
            cube,
            dialect,
            hierarchies,
            measures,
            cached["_levels"],
            use_agg=body.get("useAgg", True),
            use_cache=body.get("useCache", True),
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    if len(result["rows"]) > 1000:
        result["rows"] = result["rows"][:1000]
        result["truncated"] = True

    return jsonify(result)
