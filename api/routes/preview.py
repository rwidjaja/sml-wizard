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

# No cross-request cache here (confirmed against the user's own reference
# tool, PythonAtscaleUtility/cubes/cubes_core_functions.py: it refetches
# MDSCHEMA_* fresh on every catalog/cube selection, never caching). An
# earlier version of this file cached load_cube_metadata() indefinitely per
# (catalog, cube) - harmless for a catalog that's deployed once, but this
# wizard's own workflow is edit -> deploy -> preview -> edit -> redeploy in
# one sitting, and AtScale can rename/reassign measure and level unique_names
# across a redeploy (confirmed: a metric regenerated under the same display
# name came back with a different unique_name after a second deploy). A
# process-lifetime cache kept serving the *first* deploy's names forever,
# so a query built from the (correctly fetched, but stale) cached metadata
# referenced a measure/level unique_name that no longer existed - "works
# right after deploying, breaks after redeploying" was the exact symptom.


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

    try:
        result = load_cube_metadata(client, catalog, cube)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

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

    try:
        cube_metadata = load_cube_metadata(client, catalog, cube)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    try:
        result = run_preview_query(
            client,
            catalog,
            cube,
            dialect,
            hierarchies,
            measures,
            cube_metadata["_levels"],
            use_agg=body.get("useAgg", True),
            use_cache=body.get("useCache", True),
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    if len(result["rows"]) > 1000:
        result["rows"] = result["rows"][:1000]
        result["truncated"] = True

    return jsonify(result)
