"""SML generation engine - new code, driven entirely by the wizard's explicit
model state (see docs/BUILD_PLAN.md). Not a port of ps-utils' inference
algorithms.

Shape decisions below were corrected against `sample-dev/`, a real, working,
hand-built SML repo pulled from the user's own AtScale instance (not just the
atscale-sml-model-generator skill's docs, which turned out to disagree on one
important point):

  - **Dim-to-dim (snowflake) relationships use `type: embedded`, not
    `type: snowflake`.** They live as a `relationships:` block *inside the
    child dimension's own file* (the FK side), pointing at the parent
    dimension by name - e.g. `dimensions/Product.yml` has a relationship to
    "Product Sub Category", which itself has one to "Product Category". Each
    joined table is its own separate `object_type: dimension` file - they are
    NOT merged into one dimension with multiple `level_attributes` datasets
    (that's a valid SML pattern per the skill's Rule 5, but it's not what
    this AtScale instance's own UI produces, and it maps far more naturally
    onto this wizard's one-node-per-table model anyway).
  - **Metrics reference their fact by bare dataset `unique_name`**
    (`dataset: factinternetsalesorig`), not a `.dataset`-suffixed name.
  - **`role_play` lives on the model relationship**, e.g.
    `role_play: Order {0}` / `role_play: Ship {0}` for two FKs on one fact
    joined to the same conformed Date dimension - confirmed exactly matching
    the skill's Rule 2/21 and ps-utils' sml-serializer.ts approach.
  - Catalog `version:` is still `1.7` (skill Rule 6) even though the sample
    repo itself was `version: 1` - `sml-cli validate` flags that as a WARNING
    ("different from the latest supported version"), so 1.7 is the correct
    default to emit, not what that one sample happened to have.
"""

from __future__ import annotations

from typing import Any

import yaml

from .rules import AGG_TO_CALC_METHOD, cased, kebab, sml_data_type, title_case


def _column_key(node_id: str, column: str) -> str:
    return f"{node_id}::{column}"


def _levels_of(cfg: dict[str, dict], node_id: str) -> list[dict]:
    out = []
    prefix = f"{node_id}::"
    for key, c in cfg.items():
        if key.startswith(prefix) and c.get("dimRole") == "level":
            out.append({"key": key, "column": key[len(prefix):], "levelOrder": c.get("levelOrder", 0), "config": c})
    out.sort(key=lambda x: x["levelOrder"])
    return out


def _attached_to(cfg: dict[str, dict], level_key: str, dim_role: str) -> list[dict]:
    out = []
    for key, c in cfg.items():
        if c.get("dimRole") == dim_role and c.get("attachToKey") == level_key:
            out.append({"key": key, "config": c})
    return out


def _resolve_key_display_sort(config: dict, own_col: str, dialect: str | None) -> tuple[str, str, str | None]:
    """SML lets key_columns (join/identity), name_column (display), and
    sort_column all be different physical columns on the same level/attribute
    (e.g. key on `datekey`, display `date_name`) - `own_col` is the column
    the user actually clicked to mark as a level/secondary/alias, and
    key/display/sort each default to it unless overridden in the Inspector."""
    key_col = cased(config.get("keyColumn") or own_col, dialect)
    name_col = cased(config.get("displayColumn") or own_col, dialect)
    sort_col = cased(config["sortColumn"], dialect) if config.get("sortColumn") else None
    return key_col, name_col, sort_col


def _yaml_dump(obj: Any) -> str:
    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False, allow_unicode=True)


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_model(nodes: list[dict], joins: list[dict], cfg: dict[str, dict]) -> list[str]:
    """Validate-before-generate checks from the design README / build plan.
    Returns a list of human-readable error strings (empty = OK)."""
    errors: list[str] = []
    nodes_by_id = {n["id"]: n for n in nodes}

    for n in nodes:
        if n.get("role") not in ("fact", "dimension"):
            errors.append(f"Table '{n['table']}' has no role set (fact or dimension) - unset roles cannot be exported.")

    for n in nodes:
        if n.get("role") != "dimension":
            continue
        levels = _levels_of(cfg, n["id"])
        if not levels:
            errors.append(f"Dimension '{n.get('dimName') or n['table']}' has no hierarchy levels defined.")

    # Every secondary/alias must point at a level that actually exists on its own node.
    for key, c in cfg.items():
        if c.get("dimRole") in ("secondary", "alias"):
            node_id = key.split("::", 1)[0]
            attach = c.get("attachToKey")
            level_keys = {lv["key"] for lv in _levels_of(cfg, node_id)}
            if not attach or attach not in level_keys:
                errors.append(f"Column '{key.split('::', 1)[1]}' is a {c['dimRole']} attribute with no valid level attached.")

    # Every dimension must reach a fact, directly or through a chain of dim<->dim joins.
    fact_ids = {n["id"] for n in nodes if n.get("role") == "fact"}
    dim_ids = {n["id"] for n in nodes if n.get("role") == "dimension"}
    adjacency: dict[str, set[str]] = {nid: set() for nid in dim_ids}
    reaches_fact: set[str] = set()
    for j in joins:
        a, b = j["a"]["node"], j["b"]["node"]
        if a in fact_ids and b in dim_ids:
            reaches_fact.add(b)
        elif b in fact_ids and a in dim_ids:
            reaches_fact.add(a)
        elif a in dim_ids and b in dim_ids:
            adjacency[a].add(b)
            adjacency[b].add(a)
    changed = True
    while changed:
        changed = False
        for nid in dim_ids:
            if nid in reaches_fact:
                continue
            if adjacency[nid] & reaches_fact:
                reaches_fact.add(nid)
                changed = True
    for nid in dim_ids - reaches_fact:
        n = nodes_by_id[nid]
        errors.append(f"Dimension '{n.get('dimName') or n['table']}' is not connected to any fact table.")

    return errors


def build_sml(payload: dict[str, Any]) -> dict[str, str]:
    """payload keys: modelName, catalogName?, connectionName, asConnection,
    database, schema, dialect, nodes, joins, cfg, calculations?. Returns
    {path: yaml_text}. Raises ValidationError if the model fails
    validate_model()."""
    nodes: list[dict] = payload["nodes"]
    joins: list[dict] = payload["joins"]
    cfg: dict[str, dict] = payload.get("cfg", {})
    calculations: list[dict] = payload.get("calculations", [])
    dialect = payload.get("dialect")
    model_name = payload["modelName"]
    catalog_name = payload.get("catalogName") or f"{model_name}_catalog"
    connection_name = payload["connectionName"]

    errors = validate_model(nodes, joins, cfg)
    if errors:
        raise ValidationError(errors)

    nodes_by_id = {n["id"]: n for n in nodes}
    files: dict[str, str] = {}

    # -- catalog.yml (Rule 6: version 1.7, catalog unique_name != model unique_name, Rule 8c) --
    files["catalog.yml"] = _yaml_dump(
        {
            "unique_name": catalog_name,
            "object_type": "catalog",
            "label": catalog_name,
            "version": 1.7,
            "aggressive_agg_promotion": False,
            "build_speculative_aggs": False,
        }
    )

    # -- connections/<name>.yml --
    files[f"connections/{kebab(connection_name)}.yml"] = _yaml_dump(
        {
            "unique_name": connection_name,
            "object_type": "connection",
            "label": connection_name,
            "as_connection": payload["asConnection"],
            "database": payload["database"],
            "schema": payload["schema"],
        }
    )

    # -- datasets/<table>.yml, one per node --
    for n in nodes:
        table = cased(n["table"], dialect)
        files[f"datasets/{n['table']}.yml"] = _yaml_dump(
            {
                "unique_name": n["table"],
                "object_type": "dataset",
                "label": n["table"],
                "columns": [
                    {"name": cased(c["name"], dialect), "data_type": sml_data_type(c["type"])} for c in n["columns"]
                ],
                "connection_id": connection_name,
                "table": table,
            }
        )

    # -- dimension <-> dimension embedded relationships, grouped by the "from" node --
    # Convention: join.a is the drag origin (the FK / child side, by how the UI's
    # join-creation gesture works), join.b is the drop target (the parent/PK side).
    embedded_by_node: dict[str, list[dict]] = {n["id"]: [] for n in nodes}
    fact_dim_joins: list[dict] = []
    for j in joins:
        a_node, b_node = nodes_by_id[j["a"]["node"]], nodes_by_id[j["b"]["node"]]
        if a_node["role"] == "dimension" and b_node["role"] == "dimension":
            embedded_by_node[a_node["id"]].append(j)
        else:
            fact_dim_joins.append(j)

    def level_unique_name(config: dict, column: str) -> str:
        """`unique_name` for a level/secondary/alias - the "Query name" field
        overrides it when set, matching how it already overrides a metric's
        source column; otherwise it's just the clicked column, cased."""
        query = config.get("query")
        return cased(query, dialect) if query else cased(column, dialect)

    def leaf_level(node_id: str) -> str | None:
        levels = _levels_of(cfg, node_id)
        return level_unique_name(levels[-1]["config"], levels[-1]["column"]) if levels else None

    # -- dimensions/<dimName>.yml, one per dimension-role node --
    for n in nodes:
        if n.get("role") != "dimension":
            continue
        levels = _levels_of(cfg, n["id"])
        hier_name = n.get("hierName") or f"{n['table']} Hierarchy"
        dim_name = n.get("dimName") or n["table"]
        table = n["table"]

        level_entries = []
        level_attributes = []
        for lv in levels:
            col = level_unique_name(lv["config"], lv["column"])
            display = lv["config"].get("display") or title_case(lv["column"])
            secondaries = _attached_to(cfg, lv["key"], "secondary")
            aliases = _attached_to(cfg, lv["key"], "alias")

            secondary_attrs = []
            for s in secondaries + aliases:
                s_col_full = s["key"].split("::", 1)[1]
                s_display = s["config"].get("display") or title_case(s_col_full)
                s_key_col, s_name_col, s_sort_col = _resolve_key_display_sort(s["config"], s_col_full, dialect)
                s_attr = {
                    "unique_name": level_unique_name(s["config"], s_col_full),
                    "label": s_display,
                    "contains_unique_names": False,
                    "dataset": table,
                    "is_unique_key": False,
                    "key_columns": [s_key_col],
                    "name_column": s_name_col,
                }
                if s_sort_col:
                    s_attr["sort_column"] = s_sort_col
                secondary_attrs.append(s_attr)

            level_entry: dict[str, Any] = {"unique_name": col}
            if secondary_attrs:
                level_entry["secondary_attributes"] = secondary_attrs
            level_entries.append(level_entry)

            key_col, name_col, sort_col = _resolve_key_display_sort(lv["config"], lv["column"], dialect)
            attr: dict[str, Any] = {
                "unique_name": col,
                "label": display,
                "contains_unique_names": False,
                "dataset": table,
                "key_columns": [key_col],
                "name_column": name_col,
            }
            if sort_col:
                attr["sort_column"] = sort_col
            if n.get("isTime"):
                attr["time_unit"] = lv["config"].get("timeUnit") or lv["column"].lower()
            level_attributes.append(attr)

        # Rule: is_unique_key only when derivable - here, only for a single-level
        # dimension (matches sample-dev's Product Level, the one case that has it).
        if len(level_attributes) == 1:
            level_attributes[0]["is_unique_key"] = True

        dim_doc: dict[str, Any] = {
            "unique_name": dim_name,
            "object_type": "dimension",
            "label": dim_name,
            "hierarchies": [{"unique_name": hier_name, "label": hier_name, "levels": level_entries}],
            "level_attributes": level_attributes,
        }

        embedded_joins = embedded_by_node.get(n["id"], [])
        if embedded_joins:
            rels = []
            for j in embedded_joins:
                to_node = nodes_by_id[j["b"]["node"]]
                to_dim_name = to_node.get("dimName") or to_node["table"]
                to_leaf = leaf_level(to_node["id"])
                from_col = cased(j["a"]["column"], dialect)
                rels.append(
                    {
                        "unique_name": f"{table}_{from_col}_to_{to_dim_name}",
                        "from": {
                            "dataset": table,
                            "hierarchy": hier_name,
                            "join_columns": [from_col],
                            "level": leaf_level(n["id"]),
                        },
                        "to": {"dimension": to_dim_name, "level": to_leaf, "type": "embedded"},
                        "type": "embedded",
                    }
                )
            dim_doc["relationships"] = rels

        dim_doc["type"] = "time" if n.get("isTime") else "standard"
        files[f"dimensions/{dim_name}.yml"] = _yaml_dump(dim_doc)

    # -- metrics/<name>.yml + degenerate dimensions/<name>.yml, from fact columns --
    metric_names: list[str] = []
    degenerate_names: list[str] = []
    for n in nodes:
        if n.get("role") != "fact":
            continue
        table = n["table"]
        prefix = f"{n['id']}::"
        for key, c in cfg.items():
            if not key.startswith(prefix):
                continue
            col_name = key[len(prefix):]
            col = cased(col_name, dialect)

            if c.get("measure"):
                agg = c.get("agg", "SUM")
                calc = AGG_TO_CALC_METHOD.get(agg, "sum")
                display = c.get("display") or title_case(col_name)
                metric_unique = f"m_{table}_{col_name}_{agg.lower().replace(' ', '_')}"
                files[f"metrics/{metric_unique}.yml"] = _yaml_dump(
                    {
                        "unique_name": metric_unique,
                        "object_type": "metric",
                        "label": display,
                        "calculation_method": calc,
                        "column": c.get("query") and cased(c["query"], dialect) or col,
                        "dataset": table,
                        "unrelated_dimensions_handling": "repeat",
                    }
                )
                metric_names.append(metric_unique)

            if c.get("degen"):
                degen_display = c.get("degenDisplay") or title_case(col_name)
                degen_col = c.get("degenQuery") and cased(c["degenQuery"], dialect) or col
                degen_name = degen_display
                files[f"dimensions/{kebab(degen_name)}.yml"] = _yaml_dump(
                    {
                        "unique_name": degen_name,
                        "object_type": "dimension",
                        "label": degen_name,
                        "is_degenerate": True,
                        "hierarchies": [
                            {
                                "unique_name": f"{degen_name} Hierarchy",
                                "label": f"{degen_name} Hierarchy",
                                "levels": [{"unique_name": degen_col}],
                            }
                        ],
                        "level_attributes": [
                            {
                                "unique_name": degen_col,
                                "label": degen_display,
                                "contains_unique_names": False,
                                "dataset": table,
                                "key_columns": [degen_col],
                                "name_column": degen_col,
                            }
                        ],
                    }
                )
                degenerate_names.append(degen_name)

    # -- calculations/<name>.yml - calculated metrics (SML `metric_calc`), a
    # raw MDX expression passed through as-is (see module docstring: this
    # wizard doesn't validate MDX or build expressions for you). Confirmed
    # shape against sales-insights-postgres/calculations/*.yml, including
    # that calc unique_names go in the model's `metrics:` list alongside
    # base metrics, same as ps-utils' sml-serializer.ts convention.
    calc_names: list[str] = []
    for calc in calculations:
        unique_name = calc.get("uniqueName") or calc.get("label")
        if not unique_name:
            continue
        calc_doc: dict[str, Any] = {
            "unique_name": unique_name,
            "object_type": "metric_calc",
            "label": calc.get("label") or unique_name,
            "expression": calc.get("expression", ""),
        }
        if calc.get("description"):
            calc_doc["description"] = calc["description"]
        files[f"calculations/{kebab(unique_name)}.yml"] = _yaml_dump(calc_doc)
        calc_names.append(unique_name)

    # -- models/<modelName>.yml --
    relationships = []
    for j in fact_dim_joins:
        a_node, b_node = nodes_by_id[j["a"]["node"]], nodes_by_id[j["b"]["node"]]
        if a_node["role"] == "fact":
            fact_node, fact_col, dim_node, dim_col = a_node, j["a"]["column"], b_node, j["b"]["column"]
        else:
            fact_node, fact_col, dim_node, dim_col = b_node, j["b"]["column"], a_node, j["a"]["column"]

        dim_name = dim_node.get("dimName") or dim_node["table"]
        leaf = leaf_level(dim_node["id"])
        fact_col_cased = cased(fact_col, dialect)
        rel: dict[str, Any] = {
            "unique_name": f"{fact_node['table']}_{fact_col_cased}_to_{dim_name}",
            "from": {"dataset": fact_node["table"], "join_columns": [fact_col_cased]},
            "to": {"dimension": dim_name, "level": leaf},
        }
        role_play = j.get("rolePlay")
        if role_play:
            rel["role_play"] = f"{role_play} {{0}}"
        relationships.append(rel)

    model_doc: dict[str, Any] = {
        "unique_name": model_name,
        "object_type": "model",
        "label": model_name,
        "relationships": relationships,
        "metrics": [{"unique_name": m} for m in metric_names + calc_names],
    }
    if degenerate_names:
        model_doc["dimensions"] = degenerate_names
    files[f"models/{kebab(model_name)}.yml"] = _yaml_dump(model_doc)

    return files
