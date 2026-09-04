"""Deploy orchestration - ported from
reference/ps-utils/src/operations/atscale-deploy-catalog/AtScaleDeployCatalogOperation.ts,
using api/smlgen/catalog_xml.py (the XML compiler) and this package's
AtScaleClient (auth incl. the cookie flow, /wapi/p/repo, /wapi/git/deploy/catalog).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from smlgen.catalog_xml import build_catalog_xml
from smlgen.parse import load_sml_objects

from .client import AtScaleClient


def infer_con_ids(files: dict[str, str], connections_map: dict[str, dict[str, Any]]) -> list[str]:
    """Scans every file for `connection_id:` lines and translates each SML
    connection's unique_name to its AtScale data-warehouse connection id
    (`as_connection`) - the deploy endpoint validates conIds against the
    registered data warehouses, not SML connection names."""
    ids: set[str] = set()
    for body in files.values():
        for match in re.finditer(r"^connection_id:\s*(.+)$", body, re.MULTILINE):
            ids.add(match.group(1).strip())
    return list({connections_map.get(i, {}).get("as_connection", i) for i in ids})


def deploy(
    api_client: AtScaleClient,
    deploy_client: AtScaleClient,
    files: dict[str, str],
    repo_id: str,
    default_branch: str = "main",
    project_name: str | None = None,
) -> dict[str, Any]:
    """`api_client` must be a Bearer/JWT-authenticated AtScaleClient (for
    /wapi/p/projects/deployed); `deploy_client` must have cookie_auth=True on
    its environment (for /wapi/git/deploy/catalog) - mirrors ps-utils' dual-env
    design, since the two endpoint families use incompatible auth schemes."""
    parsed = load_sml_objects(files)
    catalog = parsed["catalog"]
    model = parsed["model"]
    if not catalog:
        raise ValueError("No catalog.yml (object_type: catalog) found among the generated files")
    if not model:
        raise ValueError("No model file (object_type: model) found among the generated files")

    con_ids = infer_con_ids(files, parsed["connections"])

    resolved_project_name = project_name or f"{catalog['unique_name']}_{default_branch}"

    deployed = api_client.list_deployed_projects()
    repo_entry = next((e for e in deployed if e.get("repoId") == repo_id), None)
    existing = next((p for p in (repo_entry or {}).get("projects", []) if p.get("name") == resolved_project_name), None)
    project_id = existing["id"] if existing else str(uuid.uuid4())

    project_xml = build_catalog_xml(
        catalog=catalog,
        model=model,
        dimensions_map=parsed["dimensions"],
        datasets_map=parsed["datasets"],
        metrics_map=parsed["metrics"],
        connections_map=parsed["connections"],
        project_name=resolved_project_name,
        project_id=project_id,
    )

    sml_raw_files = [{"relativePath": name, "rawContent": body} for name, body in files.items()]

    result = deploy_client.deploy_repo(
        repo_id=repo_id,
        sml_raw_files=sml_raw_files,
        project_xml=project_xml,
        project_name=resolved_project_name,
        con_ids=con_ids,
        project_id=project_id,
    )

    return {
        "projectId": project_id,
        "projectName": resolved_project_name,
        "repoId": repo_id,
        "conIds": con_ids,
        "reusedExistingProject": existing is not None,
        "result": result,
    }
