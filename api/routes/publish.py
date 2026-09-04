"""POST /api/publish/deploy - the Deploy button's full pipeline:
generate SML -> save to disk -> create/push the model's GitHub repo ->
attach that repo to AtScale -> compile + deploy the catalog.

Per your steer: the repo is created per-model at github.com/<git username>/
<model-name-with-dashes>, using the git credentials in connections.yaml.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from atscale.client import AtScaleClient, AtScaleEnvironment
from atscale.deploy import deploy as run_deploy
from atscale.git_ops import ensure_github_repo, push_sml_to_repo, slugify_repo_name
from config import resolve_atscale_connection, resolve_git_connection
from smlgen.build import ValidationError, build_sml

publish_bp = Blueprint("publish", __name__)

_STAGING_DIR = Path(__file__).resolve().parent.parent / ".data" / "deploys"


def _build_atscale_env(config: dict, connection_name: str, cookie_auth: bool) -> AtScaleEnvironment:
    conn = resolve_atscale_connection(config, connection_name)
    if not conn:
        raise ValueError(f"Unknown AtScale connection '{connection_name}'")
    return AtScaleEnvironment(
        base_url=conn.get("url", "").rstrip("/"),
        username=conn.get("username"),
        password=conn.get("password"),
        realm=conn.get("realm", "atscale"),
        client_id=conn.get("clientId", "atscale-ai-link"),
        client_secret=conn.get("clientSecret"),
        # The cookie-auth Keycloak form flow needs the real username/password,
        # not the API token - see AtScaleEnvironment._acquire_session_cookie.
        api_token=None if cookie_auth else conn.get("apiToken"),
        auth_type=conn.get("authType", "keycloak"),
        insecure=conn.get("insecure", True),
        cookie_auth=cookie_auth,
    )


@publish_bp.post("/publish/deploy")
def publish_deploy():
    payload = request.get_json(force=True, silent=True) or {}
    required = {"modelName", "connectionName", "asConnection", "database", "schema", "nodes", "joins"}
    missing = required - payload.keys()
    if missing:
        return jsonify({"error": f"Missing fields: {sorted(missing)}"}), 400

    config = current_app.config["SML_WIZARD_CONFIG"]
    atscale_connection_name = payload.get("atscaleConnectionName", "my_atscale")
    git_connection_name = payload.get("gitConnectionName", "git")
    private = payload.get("private", True)

    steps: dict[str, object] = {}

    # -- 1. generate --
    try:
        files = build_sml(payload)
    except ValidationError as e:
        return jsonify({"errors": e.errors}), 422
    steps["generate"] = {"fileCount": len(files)}

    # -- 2. save to disk --
    model_name = payload["modelName"]
    repo_name = slugify_repo_name(model_name)
    staging_dir = _STAGING_DIR / repo_name
    staging_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, body in files.items():
        p = staging_dir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    steps["save"] = {"path": str(staging_dir)}

    # -- 3. create/push git repo --
    git_conn = resolve_git_connection(config, git_connection_name)
    username = git_conn.get("username")
    token = git_conn.get("token")
    if not username or not token:
        return jsonify({"error": f"connections.yaml's '{git_connection_name}' connection is missing git.username/git.token", "steps": steps}), 400

    try:
        repo_info = ensure_github_repo(username, token, repo_name, private=private)
        branch = repo_info["default_branch"]
        commit_sha = push_sml_to_repo(
            staging_dir, repo_info["clone_url"], username, token, branch=branch,
            commit_message=f"Generate SML for {model_name}",
        )
    except RuntimeError as e:
        return jsonify({"error": str(e), "steps": steps}), 502
    steps["git"] = {"repoUrl": repo_info["html_url"], "branch": branch, "commit": commit_sha, "created": repo_info["created"]}

    # -- 4. attach repo to AtScale --
    try:
        api_env = _build_atscale_env(config, atscale_connection_name, cookie_auth=False)
        api_client = AtScaleClient(api_env)
        existing_repos = api_client.list_repos()
        matched = next((r for r in existing_repos if r.get("url") == repo_info["html_url"]), None)
        if matched:
            repo_id = matched["id"]
        else:
            created_repo = api_client.create_repo(
                name=repo_name, url=repo_info["html_url"], repo_type="catalog", default_branch=branch,
            )
            repo_id = created_repo["id"]
    except Exception as e:  # noqa: BLE001 - surface any attach failure to the UI
        return jsonify({"error": f"Attach to AtScale failed: {e}", "steps": steps}), 502
    steps["attach"] = {"repoId": repo_id}

    # -- 5. deploy --
    try:
        deploy_env = _build_atscale_env(config, atscale_connection_name, cookie_auth=True)
        deploy_client = AtScaleClient(deploy_env)
        deploy_result = run_deploy(api_client, deploy_client, files, repo_id, default_branch=branch)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Deploy failed: {e}", "steps": steps}), 502
    steps["deploy"] = deploy_result

    return jsonify({"ok": True, "steps": steps})
