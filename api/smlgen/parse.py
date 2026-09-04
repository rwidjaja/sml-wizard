"""Reverse of build.py: reconstructs the wizard's {nodes, joins, cfg} model
state from an existing SML repo, so the wizard can load a model someone else
already built (locally, or pulled from the Git repo AtScale is linked to)
instead of only ever starting from a blank canvas.

Known simplifications (this wizard's one-node-per-table, one-hierarchy-per-
dimension model is a deliberate subset of what SML can express - confirmed
against two real repos, sample-dev and sales-insights-postgres):

- SML's "level alias" (Rule 8 - a secondary attribute that shares its level's
  own key_columns, used as an alternate display name) and an ordinary
  secondary attribute are structurally identical once emitted (both are
  `secondary_attributes` entries) unless the alias's key_columns are checked
  against its parent level's key_columns. This parser treats every
  secondary_attributes entry as `dimRole: 'secondary'`; distinguishing a true
  alias by comparing key_columns is a follow-up, not implemented here.
- A dimension spanning *multiple physical datasets joined into one hierarchy*
  (`type: snowflake` relationships with no `to.dimension` - confirmed in
  sales-insights-postgres/dimensions/Geography Dimension.yml, e.g. Country /
  State / City / Zip each backed by a different table but forming ONE
  Geography dimension) is a different, still-valid SML pattern this wizard
  doesn't yet support authoring. Such relationships are skipped rather than
  crashing; the dimension imports using only its first dataset's columns.
- A dimension with more than one hierarchy (e.g. a Date dimension with a
  separate 445-retail-calendar hierarchy alongside the plain calendar one)
  collapses onto this wizard's single hierarchy per node - relationships
  from multiple fact FKs to different hierarchies of the same dimension can
  end up as duplicate-looking joins once resolved to a single {node, column}
  pair, since both hierarchies key off the same underlying column. Exact
  duplicates are deduped below; the extra hierarchy itself is not imported.
"""

from __future__ import annotations

from typing import Any

import yaml

from .rules import AGG_TO_CALC_METHOD

CALC_METHOD_TO_AGG = {v: k for k, v in AGG_TO_CALC_METHOD.items()}


def _load_all(files: dict[str, str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        "catalog": None,
        "connections": {},
        "datasets": {},
        "dimensions": {},
        "metrics": {},
        "model": None,
    }
    for path, body in files.items():
        doc = yaml.safe_load(body)
        if not isinstance(doc, dict):
            continue
        object_type = doc.get("object_type")
        if object_type == "catalog":
            parsed["catalog"] = doc
        elif object_type == "connection":
            parsed["connections"][doc["unique_name"]] = doc
        elif object_type == "dataset":
            parsed["datasets"][doc["unique_name"]] = doc
        elif object_type == "dimension":
            parsed["dimensions"][doc["unique_name"]] = doc
        elif object_type == "metric":
            parsed["metrics"][doc["unique_name"]] = doc
        elif object_type == "model":
            parsed["model"] = doc
    return parsed


#: Exposed for the deploy pipeline (api/atscale/deploy.py), which needs the
#: same catalog/model/dimensions/datasets/metrics/connections maps this
#: function builds internally, to compile the legacy catalog XML.
load_sml_objects = _load_all


def parse_sml(files: dict[str, str]) -> dict[str, Any]:
    parsed = _load_all(files)
    datasets = parsed["datasets"]
    dimensions = parsed["dimensions"]
    metrics = parsed["metrics"]
    model = parsed["model"] or {}

    # -- classify each dataset: fact (backs a metric), dimension (backs a
    # non-degenerate dimension's level_attributes), or unused --
    fact_dataset_names: set[str] = {m["dataset"] for m in metrics.values() if "dataset" in m}
    dim_by_dataset: dict[str, dict] = {}
    degenerate_dims: list[dict] = []
    for dim in dimensions.values():
        if dim.get("is_degenerate"):
            degenerate_dims.append(dim)
            continue
        level_attrs = dim.get("level_attributes") or []
        if level_attrs:
            dim_by_dataset[level_attrs[0]["dataset"]] = dim

    node_ids: dict[str, str] = {}  # dataset unique_name -> node id
    nodes: list[dict] = []
    seq = 0

    def node_for_dataset(dataset_name: str, role: str) -> str:
        nonlocal seq
        if dataset_name in node_ids:
            return node_ids[dataset_name]
        ds = datasets.get(dataset_name, {})
        node_id = f"n{seq}"
        seq += 1
        node_ids[dataset_name] = node_id
        col = seq % 3
        row = seq // 3
        node: dict[str, Any] = {
            "id": node_id,
            "schema": "",
            "table": dataset_name,
            "columns": [{"name": c["name"], "type": c.get("data_type", "string")} for c in ds.get("columns", [])],
            "x": 40 + col * 320,
            "y": 40 + row * 300,
            "role": role,
        }
        nodes.append(node)
        return node_id

    cfg: dict[str, dict] = {}
    joins: list[dict] = []
    join_seq = 0

    # -- dimension nodes: levels, secondary attributes, embedded (snowflake) relationships --
    dim_leaf_col: dict[str, str] = {}  # dimension unique_name -> its leaf level's source column
    for dataset_name, dim in dim_by_dataset.items():
        node_id = node_for_dataset(dataset_name, "dimension")
        nodes[[n["id"] for n in nodes].index(node_id)]["dimName"] = dim["unique_name"]
        nodes[[n["id"] for n in nodes].index(node_id)]["isTime"] = dim.get("type") == "time"

        level_attrs_by_name = {la["unique_name"]: la for la in dim.get("level_attributes", [])}
        hierarchies = dim.get("hierarchies") or []
        if hierarchies:
            nodes[[n["id"] for n in nodes].index(node_id)]["hierName"] = hierarchies[0].get("unique_name")
            levels = hierarchies[0].get("levels") or []
            for idx, level_entry in enumerate(levels):
                level_name = level_entry["unique_name"]
                attr = level_attrs_by_name.get(level_name)
                if not attr:
                    continue
                col = attr["key_columns"][0]
                key = f"{node_id}::{col}"
                cfg[key] = {"dimRole": "level", "levelOrder": idx, "display": attr.get("label")}
                if idx == len(levels) - 1:
                    dim_leaf_col[dim["unique_name"]] = col

                for sec in level_entry.get("secondary_attributes") or []:
                    sec_col = sec["key_columns"][0]
                    sec_key = f"{node_id}::{sec_col}"
                    cfg[sec_key] = {"dimRole": "secondary", "attachToKey": key, "display": sec.get("label")}

        for rel in dim.get("relationships") or []:
            # `type: embedded` (cross-dimension link, `to.dimension` set) is what
            # this wizard's one-node-per-table model can represent. `type:
            # snowflake` (intra-dimension: several physical datasets merged into
            # ONE dimension's own hierarchy, `to.dimension` absent - confirmed in
            # a real repo, sales-insights-postgres/dimensions/Geography
            # Dimension.yml) is a different, still-valid modeling pattern this
            # wizard doesn't yet support authoring - skip rather than crash, so
            # an import of a repo using it still succeeds for everything else.
            to_dim_name = rel["to"].get("dimension")
            if not to_dim_name:
                continue
            to_dim = dimensions.get(to_dim_name)
            if not to_dim:
                continue
            to_dataset = (to_dim.get("level_attributes") or [{}])[0].get("dataset")
            to_node_id = node_for_dataset(to_dataset, "dimension") if to_dataset else None
            if not to_node_id:
                continue
            from_col = rel["from"]["join_columns"][0]
            to_level_name = rel["to"]["level"]
            to_attr = {la["unique_name"]: la for la in (to_dim.get("level_attributes") or [])}.get(to_level_name)
            to_col = to_attr["key_columns"][0] if to_attr else to_level_name
            joins.append(
                {
                    "id": f"j{join_seq}",
                    "a": {"node": node_id, "column": from_col},
                    "b": {"node": to_node_id, "column": to_col},
                }
            )
            join_seq += 1

    # -- fact nodes: metrics, degenerate dimensions --
    for metric in metrics.values():
        dataset_name = metric.get("dataset")
        if not dataset_name:
            continue
        node_id = node_for_dataset(dataset_name, "fact")
        nodes[[n["id"] for n in nodes].index(node_id)].setdefault(
            "factName", f"{dataset_name} Facts"
        )
        col = metric.get("column")
        key = f"{node_id}::{col}"
        agg = CALC_METHOD_TO_AGG.get(metric.get("calculation_method"), "SUM")
        cfg[key] = {"measure": True, "agg": agg, "display": metric.get("label"), "query": col}

    for degen in degenerate_dims:
        level_attrs = degen.get("level_attributes") or []
        if not level_attrs:
            continue
        dataset_name = level_attrs[0]["dataset"]
        node_id = node_ids.get(dataset_name)
        if not node_id:
            node_id = node_for_dataset(dataset_name, "fact")
        col = level_attrs[0]["key_columns"][0]
        key = f"{node_id}::{col}"
        existing = cfg.get(key, {})
        existing.update({"degen": True, "degenDisplay": level_attrs[0].get("label"), "degenQuery": col})
        cfg[key] = existing

    # -- model relationships: fact<->dim joins, with role_play --
    for rel in model.get("relationships") or []:
        from_dataset = rel["from"]["dataset"]
        from_node = node_ids.get(from_dataset) or node_for_dataset(from_dataset, "fact")
        from_col = rel["from"]["join_columns"][0]

        to_dim_name = rel["to"]["dimension"]
        to_dim = dimensions.get(to_dim_name)
        if not to_dim:
            continue
        to_dataset = (to_dim.get("level_attributes") or [{}])[0].get("dataset")
        to_node = node_ids.get(to_dataset)
        if not to_node:
            continue
        to_level_name = rel["to"]["level"]
        to_attr = {la["unique_name"]: la for la in (to_dim.get("level_attributes") or [])}.get(to_level_name)
        to_col = to_attr["key_columns"][0] if to_attr else to_level_name

        join: dict[str, Any] = {
            "id": f"j{join_seq}",
            "a": {"node": from_node, "column": from_col},
            "b": {"node": to_node, "column": to_col},
        }
        join_seq += 1
        role_play = rel.get("role_play")
        if role_play:
            join["rolePlay"] = role_play.rsplit(" {0}", 1)[0]
        joins.append(join)

    joins = _dedupe_joins(joins)
    return {"nodes": nodes, "joins": joins, "cfg": cfg}


def _dedupe_joins(joins: list[dict]) -> list[dict]:
    """Collapses joins that resolve to the identical {node, column} pair on
    both sides (direction-insensitive) - see the multi-hierarchy note above."""
    seen: set[tuple] = set()
    out = []
    for j in joins:
        a = (j["a"]["node"], j["a"]["column"])
        b = (j["b"]["node"], j["b"]["column"])
        sig = (a, b, j.get("rolePlay")) if a <= b else (b, a, j.get("rolePlay"))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(j)
    return out
