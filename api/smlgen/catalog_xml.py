"""Python port of reference/ps-utils/src/algorithm/catalog-xml-builder.ts.

Builds the legacy AtScale catalog XML (project_2_0 schema) that
`/wapi/git/deploy/catalog` requires alongside the raw SML files, as its
`projectXml` field. Object UUIDs are derived deterministically (UUID v5) from
the object's unique name and the project name, so repeated calls with
identical SML produce identical XML - ported line-for-line from the TS
original, including its "assume single join key" simplification (see
`joinKa = keyedAttrs[0]` below), which is fine for this wizard's one join per
fact-dimension pair.
"""

from __future__ import annotations

import uuid
from typing import Any
from xml.sax.saxutils import escape as _esc

# The TS original's ROOT_NS ("6ba7b810-9dad-11d1-80b4-00c04fd430c8") is
# Python's uuid.NAMESPACE_URL - same constant.
ROOT_NS = uuid.NAMESPACE_URL

_SML_TO_XML_TYPE = {
    "string": "String",
    "int": "Int",
    "long": "Long",
    "double": "Double",
    "float": "Float",
    "decimal": "Decimal",
    "boolean": "Boolean",
    "date": "Date",
    "datetime": "DateTime",
}

_AGG_MAP = {
    "SUM": "SUM",
    "AVERAGE": "AVG",
    "MINIMUM": "MIN",
    "MAXIMUM": "MAX",
    "COUNT NON-NULL": "COUNT",
    "COUNT": "COUNT",
}


def _sml_type_to_xml(sml_type: str | None) -> str:
    return _SML_TO_XML_TYPE.get((sml_type or "string").lower(), "String")


def _project_namespace(project_name: str) -> uuid.UUID:
    return uuid.uuid5(ROOT_NS, f"atscale-project:{project_name}")


def _gen_id(ns: uuid.UUID, path: str) -> str:
    return str(uuid.uuid5(ns, path))


def build_catalog_xml(
    catalog: dict[str, Any],
    model: dict[str, Any],
    dimensions_map: dict[str, dict[str, Any]],
    datasets_map: dict[str, dict[str, Any]],
    metrics_map: dict[str, dict[str, Any]],
    connections_map: dict[str, dict[str, Any]],
    project_name: str,
    project_id: str | None = None,
) -> str:
    engine_id = project_id or str(uuid.uuid4())
    ns = _project_namespace(project_name)
    caption = catalog.get("label") or catalog.get("unique_name") or "catalog"

    default_database = "default"
    default_schema = "default"
    for conn in connections_map.values():
        if conn.get("database"):
            default_database = conn["database"]
        if conn.get("schema"):
            default_schema = conn["schema"]
        break

    def as_connection_id(conn_id: str | None) -> str:
        if not conn_id:
            return "default"
        conn = connections_map.get(conn_id)
        return (conn.get("as_connection") if conn else None) or conn_id

    # -- model relationships --
    relationships = [
        {
            "fromDataset": (r.get("from") or {}).get("dataset", ""),
            "joinColumns": (r.get("from") or {}).get("join_columns", []),
            "toDimension": (r.get("to") or {}).get("dimension", ""),
            "toLevel": (r.get("to") or {}).get("level", ""),
        }
        for r in model.get("relationships") or []
    ]

    ref_dim_names = {r["toDimension"] for r in relationships if r["toDimension"]}
    fact_dataset_names = {r["fromDataset"] for r in relationships if r["fromDataset"]}

    # -- keyed attributes (join targets) --
    keyed_attrs: list[dict[str, Any]] = []
    keyed_attr_by_level: dict[str, dict[str, Any]] = {}

    for dim_name in ref_dim_names:
        dim = dimensions_map.get(dim_name)
        if not dim:
            continue
        la_map = {la["unique_name"]: la for la in dim.get("level_attributes") or []}
        for rel in relationships:
            if rel["toDimension"] != dim_name:
                continue
            la = la_map.get(rel["toLevel"])
            if not la:
                continue
            key_id = _gen_id(ns, f"{dim_name}.{rel['toLevel']}.key")
            attr_id = _gen_id(ns, f"{dim_name}.{rel['toLevel']}.attr")
            ds_id = _gen_id(ns, la.get("dataset") or "")
            ka = {
                "laUniqueName": la["unique_name"],
                "datasetName": la.get("dataset") or "",
                "columnName": la.get("name_column") or (la.get("key_columns") or [la["unique_name"]])[0],
                "label": la.get("label") or la["unique_name"],
                "keyId": key_id,
                "attrId": attr_id,
                "datasetId": ds_id,
            }
            keyed_attrs.append(ka)
            keyed_attr_by_level[rel["toLevel"]] = ka

    # -- fact datasets with metric columns --
    fact_datasets: dict[str, dict[str, Any]] = {}
    for ds_name in fact_dataset_names:
        ds = datasets_map.get(ds_name)
        if not ds:
            continue
        fact_datasets[ds_name] = {
            "datasetName": ds_name,
            "ds": ds,
            "dsId": _gen_id(ns, ds_name),
            "metrics": [],
            "joinColName": next((r["joinColumns"][0] for r in relationships if r["fromDataset"] == ds_name and r["joinColumns"]), None),
        }

    model_cube_name = model.get("unique_name") or model.get("label") or "model"
    for metric_ref in model.get("metrics") or []:
        mn = metric_ref if isinstance(metric_ref, str) else metric_ref.get("unique_name")
        m = metrics_map.get(mn)
        if not m:
            continue
        fde = fact_datasets.get(m.get("dataset"))
        if not fde:
            continue
        fde["metrics"].append(
            {"unique_name": m["unique_name"], "column": m.get("column"), "attrId": _gen_id(ns, f"{model_cube_name}.{m['unique_name']}")}
        )

    # -- dimension datasets --
    dim_datasets: list[dict[str, Any]] = []
    seen_dim_ds: set[str] = set()
    for ka in keyed_attrs:
        if ka["datasetName"] in seen_dim_ds:
            continue
        seen_dim_ds.add(ka["datasetName"])
        ds = datasets_map.get(ka["datasetName"])
        if not ds:
            continue
        dim_datasets.append({"datasetName": ka["datasetName"], "ds": ds, "dsId": ka["datasetId"], "ka": ka})

    # -- dimensions XML --
    dimensions_xml_parts = []
    for dim_name in ref_dim_names:
        dim = dimensions_map.get(dim_name)
        if not dim:
            continue
        dim_id = _gen_id(ns, dim_name)
        hier_parts = []
        for h in dim.get("hierarchies") or []:
            for level in h.get("levels") or []:
                ka = keyed_attr_by_level.get(level["unique_name"])
                if not ka:
                    continue
                hier_id = _gen_id(ns, f"{dim_name}.{h['unique_name']}")
                hier_parts.append(
                    f"""
      <hierarchy id="{hier_id}" name="{_esc(h['unique_name'])}">
        <properties>
          <caption>{_esc(h.get('label') or h['unique_name'])}</caption>
          <visible>true</visible>
          <filter-empty>Always</filter-empty>
          <default-member><all-member></all-member></default-member>
        </properties>
        <level primary-attribute="{ka['attrId']}">
          <properties>
            <unique-in-parent>false</unique-in-parent>
            <visible>true</visible>
          </properties>
        </level>
      </hierarchy>"""
                )
        hierarchies_xml = "".join(hier_parts)
        if not hierarchies_xml:
            continue
        dimensions_xml_parts.append(
            f"""
  <dimension id="{dim_id}" name="{_esc(dim_name)}">
    <properties>
      <visible>true</visible>
      <caption>{_esc(dim.get('label') or dim_name)}</caption>
      <dimension-type>Other</dimension-type>
    </properties>{hierarchies_xml}
  </dimension>"""
        )
    dimensions_xml = "".join(dimensions_xml_parts)

    def columns_xml(ds: dict[str, Any]) -> str:
        return "".join(
            f"\n        <column><name>{_esc(c['name'])}</name><type>{_sml_type_to_xml(c.get('data_type'))}</type></column>"
            for c in ds.get("columns") or []
        )

    dim_datasets_xml_parts = []
    for entry in dim_datasets:
        ds_name, ds, ds_id, ka = entry["datasetName"], entry["ds"], entry["dsId"], entry["ka"]
        table_name = ds.get("table") or ds_name.replace(".dataset", "")
        conn_id = as_connection_id(ds.get("connection_id"))
        dim_datasets_xml_parts.append(
            f"""
  <data-set id="{ds_id}" name="{_esc(ds_name)}">
    <properties><allow-aggregates>true</allow-aggregates></properties>
    <physical>
      <connection id="{_esc(conn_id)}"></connection>
      <table>
        <database>{_esc(default_database)}</database>
        <schema>{_esc(default_schema)}</schema>
        <name>{_esc(table_name)}</name>
      </table>
      <immutable>false</immutable>{columns_xml(ds)}
    </physical>
    <logical>
      <key-ref id="{ka['keyId']}" unique="false" complete="true">
        <column>{_esc(ka['columnName'])}</column>
      </key-ref>
      <attribute-ref id="{ka['attrId']}" complete="true">
        <column>{_esc(ka['columnName'])}</column>
      </attribute-ref>
    </logical>
  </data-set>"""
        )
    dim_datasets_xml = "".join(dim_datasets_xml_parts)

    fact_datasets_xml_parts = []
    for fde in fact_datasets.values():
        ds_name, ds, ds_id = fde["datasetName"], fde["ds"], fde["dsId"]
        table_name = ds.get("table") or ds_name.replace(".dataset", "")
        conn_id = as_connection_id(ds.get("connection_id"))
        fact_datasets_xml_parts.append(
            f"""
  <data-set id="{ds_id}" name="{_esc(ds_name)}">
    <properties><allow-aggregates>true</allow-aggregates></properties>
    <physical>
      <connection id="{_esc(conn_id)}"></connection>
      <table>
        <database>{_esc(default_database)}</database>
        <schema>{_esc(default_schema)}</schema>
        <name>{_esc(table_name)}</name>
      </table>
      <immutable>false</immutable>{columns_xml(ds)}
    </physical>
    <logical></logical>
  </data-set>"""
        )
    fact_datasets_xml = "".join(fact_datasets_xml_parts)

    # -- cube XML --
    cube_id = _gen_id(ns, model_cube_name)

    measure_attrs_parts = []
    for fde in fact_datasets.values():
        for m in fde["metrics"]:
            mn = metrics_map.get(m["unique_name"])
            agg = (mn.get("calculation_method") if mn else "sum").upper()
            def_agg = _AGG_MAP.get(agg, "SUM")
            measure_attrs_parts.append(
                f"""
      <attribute id="{m['attrId']}" name="{_esc(m['unique_name'])}">
        <properties>
          <visible>true</visible>
          <caption>{_esc((mn or {}).get('label') or m['unique_name'])}</caption>
          <type><measure><default-aggregation>{def_agg}</default-aggregation></measure></type>
        </properties>
      </attribute>"""
            )
    measure_attrs_xml = "".join(measure_attrs_parts)

    join_ka = keyed_attrs[0] if keyed_attrs else None
    cube_ds_refs_parts = []
    for fde in fact_datasets.values():
        key_ref = (
            f"\n          <key-ref id=\"{join_ka['keyId']}\" unique=\"false\" complete=\"false\"><column>{_esc(fde.get('joinColName') or '')}</column></key-ref>"
            if join_ka
            else ""
        )
        attr_refs = "".join(
            f"\n          <attribute-ref id=\"{m['attrId']}\" complete=\"true\"><column>{_esc(m['column'])}</column></attribute-ref>"
            for m in fde["metrics"]
        )
        cube_ds_refs_parts.append(
            f"""
    <data-set-ref id="{fde['dsId']}">
      <logical>{key_ref}{attr_refs}
      </logical>
    </data-set-ref>"""
        )
    cube_ds_refs_xml = "".join(cube_ds_refs_parts)

    cubes_xml = f"""
  <cube id="{cube_id}" name="{_esc(model_cube_name)}">
    <properties>
      <caption>{_esc(model.get('label') or model_cube_name)}</caption>
      <visible>true</visible>
    </properties>
    <attributes>{measure_attrs_xml}
    </attributes>
    <data-sets>{cube_ds_refs_xml}
    </data-sets>
    <calculated-members></calculated-members>
  </cube>"""

    attrs_xml = "".join(
        f"""
  <attribute-key id="{ka['keyId']}"><properties><visible>true</visible><columns>1</columns></properties></attribute-key>
  <keyed-attribute id="{ka['attrId']}" key-ref="{ka['keyId']}" name="{_esc(ka['laUniqueName'])}"><properties><visible>true</visible><caption>{_esc(ka['label'])}</caption><type><enum></enum></type><ordering><sort-key><order>ascending</order><value></value></sort-key></ordering></properties></keyed-attribute>"""
        for ka in keyed_attrs
    )

    return (
        '<schema xmlns="http://www.atscale.com/xsd/project_2_0"'
        f' name="{_esc(project_name)}" version="2.0"'
        ' xsi:schemaLocation="http://www.atscale.com/xsd/project_2_0 ../../../../../core/src/main/resources/com/atscale/engine/schema/project_2_0.xsd"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        "<annotations>"
        '<annotation name="migrationVersion">2020.3.0.1</annotation>'
        f'<annotation name="engineId">{engine_id}</annotation>'
        '<annotation name="version">version-to-be-generated-on-deploy</annotation>'
        "</annotations>"
        "<properties>"
        "<visible>true</visible>"
        f"<caption>{_esc(caption)}</caption>"
        "<aggregate-prediction><speculative-aggregates>false</speculative-aggregates></aggregate-prediction>"
        "</properties>"
        f"<attributes>{attrs_xml}\n</attributes>"
        f"<dimensions>{dimensions_xml}\n</dimensions>"
        f"<data-sets>{dim_datasets_xml}{fact_datasets_xml}\n</data-sets>"
        "<calculated-members></calculated-members>"
        f"<cubes>{cubes_xml}\n</cubes>"
        "</schema>"
    )
