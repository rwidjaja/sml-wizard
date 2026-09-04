"""Fixture mirrors the SalesInsights model (dimproduct -> dimproductcategory
snowflake, factinternetsales -> Product, factinternetsales -> Date with
Order/Ship role-play) built live in the browser during development and cross-
checked against `sample-dev/`, a real hand-built SML repo from the user's own
AtScale instance - see smlgen/build.py's module docstring for what that
repo corrected in this generator's design.
"""

from __future__ import annotations

import shutil

import pytest
import yaml

from smlgen.build import ValidationError, build_sml, validate_model
from smlgen.parse import parse_sml
from smlgen.validate import validate_sml

PAYLOAD = {
    "modelName": "sample_dev_test",
    "connectionName": "con_atscale_data_SalesInsights",
    "asConnection": "PostgresDB",
    "database": "atscale_data",
    "schema": "SalesInsights",
    "dialect": "postgresql",
    "nodes": [
        {
            "id": "n0",
            "schema": "SalesInsights",
            "table": "dimproduct",
            "role": "dimension",
            "dimName": "Product",
            "hierName": "Product Hierarchy",
            "columns": [
                {"name": "productkey", "type": "Int"},
                {"name": "englishproductname", "type": "String"},
                {"name": "listprice", "type": "Float"},
                {"name": "startdate", "type": "String"},
                {"name": "productsubcategorykey", "type": "Int"},
                {"name": "productline", "type": "String"},
                {"name": "productsubcategoryname", "type": "String"},
            ],
        },
        {
            "id": "n1",
            "schema": "SalesInsights",
            "table": "dimproductcategory",
            "role": "dimension",
            "dimName": "Product Category",
            "hierName": "Product Category Hierarchy",
            "columns": [
                {"name": "productcategorykey", "type": "Int"},
                {"name": "productcategoryalternatekey", "type": "Int"},
                {"name": "productcategoryname", "type": "String"},
            ],
        },
        {
            "id": "n2",
            "schema": "SalesInsights",
            "table": "datecustom",
            "role": "dimension",
            "dimName": "Date",
            "hierName": "Date Hierarchy",
            "isTime": True,
            "columns": [
                {"name": "pk_date", "type": "DateTime"},
                {"name": "datekey", "type": "Long"},
                {"name": "year", "type": "DateTime"},
                {"name": "year_name", "type": "String"},
                {"name": "quarter", "type": "DateTime"},
                {"name": "quarter_name", "type": "String"},
                {"name": "month", "type": "DateTime"},
                {"name": "month_name", "type": "String"},
            ],
        },
        {
            "id": "n3",
            "schema": "SalesInsights",
            "table": "factinternetsales",
            "role": "fact",
            "factName": "Factinternetsales Facts",
            "columns": [
                {"name": "salesordernumber", "type": "String"},
                {"name": "productkey", "type": "Int"},
                {"name": "orderdatekey", "type": "Int"},
                {"name": "customerkey", "type": "Int"},
                {"name": "orderquantity", "type": "Int"},
                {"name": "unitprice", "type": "Float"},
                {"name": "salesamount", "type": "Float"},
                {"name": "taxamt", "type": "Float"},
                {"name": "shipdatekey", "type": "Int"},
                {"name": "currencykey", "type": "Int"},
            ],
        },
    ],
    "joins": [
        {"id": "j0", "a": {"node": "n0", "column": "productsubcategorykey"}, "b": {"node": "n1", "column": "productcategorykey"}},
        {"id": "j1", "a": {"node": "n3", "column": "productkey"}, "b": {"node": "n0", "column": "productkey"}},
        {"id": "j2", "a": {"node": "n3", "column": "orderdatekey"}, "b": {"node": "n2", "column": "datekey"}, "rolePlay": "Order"},
        {"id": "j3", "a": {"node": "n3", "column": "shipdatekey"}, "b": {"node": "n2", "column": "datekey"}, "rolePlay": "Ship"},
    ],
    "cfg": {
        "n0::productkey": {"dimRole": "level", "levelOrder": 0, "display": "Product"},
        "n1::productcategorykey": {"dimRole": "level", "levelOrder": 0, "display": "Product Category"},
        "n2::year": {"dimRole": "level", "levelOrder": 0, "display": "Year"},
        "n2::quarter": {"dimRole": "level", "levelOrder": 1, "display": "Quarter"},
        "n2::month": {"dimRole": "level", "levelOrder": 2, "display": "Month"},
        "n2::year_name": {"dimRole": "secondary", "attachToKey": "n2::year", "display": "Year Name"},
        "n2::quarter_name": {"dimRole": "secondary", "attachToKey": "n2::quarter", "display": "Quarter Name"},
        "n2::month_name": {"dimRole": "secondary", "attachToKey": "n2::month", "display": "Month Name"},
        "n3::orderquantity": {"measure": True, "agg": "SUM", "display": "Order Quantity", "query": "orderquantity"},
        "n3::unitprice": {"measure": True, "agg": "SUM", "display": "Unit Price", "query": "unitprice"},
        "n3::salesamount": {"measure": True, "agg": "SUM", "display": "Sales Amount", "query": "salesamount"},
    },
}


def test_validate_model_passes():
    assert validate_model(PAYLOAD["nodes"], PAYLOAD["joins"], PAYLOAD["cfg"]) == []


def test_missing_role_is_an_error():
    nodes = [dict(PAYLOAD["nodes"][0])]
    nodes[0]["role"] = None
    errors = validate_model(nodes, [], {})
    assert any("no role set" in e for e in errors)


def test_unreachable_dimension_is_an_error():
    # A dimension with a level but no join to any fact.
    nodes = [dict(PAYLOAD["nodes"][1])]
    cfg = {"n1::productcategorykey": {"dimRole": "level", "levelOrder": 0}}
    errors = validate_model(nodes, [], cfg)
    assert any("not connected to any fact" in e for e in errors)


def test_build_sml_snowflake_embedded_relationship():
    files = build_sml(PAYLOAD)
    product = yaml.safe_load(files["dimensions/Product.yml"])
    assert product["relationships"][0]["type"] == "embedded"
    assert product["relationships"][0]["to"]["dimension"] == "Product Category"
    # Rule: the model file's own relationships list has fact<->dim only, never
    # the dim<->dim embedded relationship (that lives on the dimension file).
    model = yaml.safe_load(files["models/sample-dev-test.yml"])
    dim_targets = [r["to"]["dimension"] for r in model["relationships"]]
    assert dim_targets.count("Product Category") == 0


def test_build_sml_role_play():
    files = build_sml(PAYLOAD)
    model = yaml.safe_load(files["models/sample-dev-test.yml"])
    role_plays = {r["unique_name"]: r.get("role_play") for r in model["relationships"]}
    assert role_plays["factinternetsales_orderdatekey_to_Date"] == "Order {0}"
    assert role_plays["factinternetsales_shipdatekey_to_Date"] == "Ship {0}"


def test_build_sml_time_dimension():
    files = build_sml(PAYLOAD)
    date_dim = yaml.safe_load(files["dimensions/Date.yml"])
    assert date_dim["type"] == "time"
    assert all("time_unit" in a for a in date_dim["level_attributes"])


def test_build_sml_catalog_version_is_1_7():
    files = build_sml(PAYLOAD)
    catalog = yaml.safe_load(files["catalog.yml"])
    assert catalog["version"] == 1.7
    assert catalog["unique_name"] != PAYLOAD["modelName"]


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx/node not available")
def test_generated_sml_passes_sml_cli_validate():
    files = build_sml(PAYLOAD)
    result = validate_sml(files)
    assert result["passed"], result["output"]


def test_parse_sml_round_trips_build_sml_output():
    files = build_sml(PAYLOAD)
    result = parse_sml(files)

    by_table = {n["table"]: n for n in result["nodes"]}
    assert by_table["dimproduct"]["role"] == "dimension"
    assert by_table["dimproduct"]["dimName"] == "Product"
    assert by_table["factinternetsales"]["role"] == "fact"
    assert by_table["datecustom"]["isTime"] is True

    node_id_by_table = {n["table"]: n["id"] for n in result["nodes"]}
    role_plays = {
        (j["a"]["column"], j["b"]["column"]): j.get("rolePlay")
        for j in result["joins"]
        if j["a"]["node"] == node_id_by_table["factinternetsales"]
    }
    assert role_plays[("orderdatekey", "month")] == "Order"
    assert role_plays[("shipdatekey", "month")] == "Ship"

    # Snowflake (embedded) dim<->dim join survives the round trip.
    snowflake = [
        j
        for j in result["joins"]
        if j["a"]["node"] == node_id_by_table["dimproduct"] and j["b"]["node"] == node_id_by_table["dimproductcategory"]
    ]
    assert len(snowflake) == 1

    date_node_id = node_id_by_table["datecustom"]
    levels = [v for k, v in result["cfg"].items() if k.startswith(f"{date_node_id}::") and v.get("dimRole") == "level"]
    levels.sort(key=lambda v: v["levelOrder"])
    assert [v["levelOrder"] for v in levels] == [0, 1, 2]
    secondaries = [v for v in result["cfg"].values() if v.get("dimRole") == "secondary"]
    assert len(secondaries) == 3
