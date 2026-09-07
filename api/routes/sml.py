"""POST /api/sml/generate, POST /api/sml/validate, POST /api/sml/import*."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from config import WORKSPACE_ROOT, model_workspace_dir, resolve_git_connection
from routes.session import get_client
from smlgen.build import ValidationError, build_sml
from smlgen.naming import is_valid_model_name
from smlgen.parse import parse_sml
from smlgen.validate import SmlCliNotFound, validate_sml

sml_bp = Blueprint("sml", __name__)

_YAML_SUFFIXES = {".yml", ".yaml"}


def _read_sml_directory(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in _YAML_SUFFIXES:
            files[str(path.relative_to(root))] = path.read_text(encoding="utf-8")
    return files


def write_files(root: Path, files: list[dict[str, str]]) -> int:
    """Writes a /sml/generate-shaped `[{name, body}]` file list under `root`.
    Shared by /sml/save and publish.py's deploy pipeline so both stage into
    the same directory layout."""
    root.mkdir(parents=True, exist_ok=True)
    for f in files:
        p = root / f["name"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f["body"], encoding="utf-8")
    return len(files)


@sml_bp.post("/sml/generate")
def generate():
    payload = request.get_json(force=True, silent=True) or {}
    required = {"modelName", "connectionName", "asConnection", "database", "schema", "nodes", "joins"}
    missing = required - payload.keys()
    if missing:
        return jsonify({"error": f"Missing fields: {sorted(missing)}"}), 400

    try:
        files = build_sml(payload)
    except ValidationError as e:
        return jsonify({"errors": e.errors}), 422

    return jsonify({"files": [{"name": name, "body": body} for name, body in sorted(files.items())]})


@sml_bp.post("/sml/validate")
def validate():
    payload = request.get_json(force=True, silent=True) or {}
    files = payload.get("files")
    if not files:
        return jsonify({"error": "Missing 'files' - pass the array returned by /api/sml/generate"}), 400

    file_map = {f["name"]: f["body"] for f in files}
    try:
        result = validate_sml(file_map)
    except SmlCliNotFound as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@sml_bp.post("/sml/save-path")
def save_path():
    """Writes generated SML files to a local directory - saving IS generating
    SML and persisting it somewhere real (disk, or a repo via git), not a
    separate proprietary state format."""
    payload = request.get_json(force=True, silent=True) or {}
    raw_path = payload.get("path")
    files = payload.get("files")
    if not raw_path or not files:
        return jsonify({"error": "Missing 'path' or 'files'"}), 400

    root = Path(raw_path).expanduser()
    count = write_files(root, files)
    return jsonify({"ok": True, "path": str(root), "count": count})


@sml_bp.post("/sml/save")
def save():
    """Writes generated SML files into this model's workspace directory
    (workspace/<slugified-model-name>/) - the default, no-path-typing save
    path. Same destination publish.py's deploy pipeline stages into, so
    Save and Deploy operate on one persistent working copy per model."""
    payload = request.get_json(force=True, silent=True) or {}
    model_name = payload.get("modelName")
    files = payload.get("files")
    if not model_name or not files:
        return jsonify({"error": "Missing 'modelName' or 'files'"}), 400
    if not is_valid_model_name(model_name):
        return jsonify({"error": f"'{model_name}' is not a valid model name - use letters, numbers, '-' or '_' only"}), 400

    root = model_workspace_dir(model_name)
    count = write_files(root, files)
    return jsonify({"ok": True, "path": str(root), "count": count})


def _strip_credentials(url: str) -> str:
    """Drops a `user:token@` userinfo segment - origin remotes in this app
    always carry embedded credentials (see import_git/git_ops.push_sml_to_repo),
    which must never reach the browser."""
    import re

    return re.sub(r"^(https?://)[^/@]+@", r"\1", url)


def _git_remote_of(path: Path) -> tuple[str, str] | None:
    """(url, branch) of `path`'s origin remote, if it's a real git clone -
    e.g. one /sml/import-git cloned directly into the workspace. Lets the
    Load tab's workspace picker resume a pulled model as an update (commit +
    push to the same history) instead of publish.py treating it as a brand
    new repo and getting a non-fast-forward rejection."""
    if not (path / ".git").exists():
        return None
    from git import InvalidGitRepositoryError, Repo

    try:
        repo = Repo(str(path))
        if "origin" not in [r.name for r in repo.remotes]:
            return None
        url = _strip_credentials(next(repo.remotes.origin.urls))
        branch = repo.active_branch.name if not repo.head.is_detached else "main"
        return url, branch
    except (InvalidGitRepositoryError, TypeError, ValueError):
        return None


@sml_bp.get("/sml/models")
def list_workspace_models():
    """Lists models already saved under the workspace root, for the Load
    tab's local-workspace picker - no manual path typing required."""
    if not WORKSPACE_ROOT.is_dir():
        return jsonify([])
    out = []
    for child in sorted(WORKSPACE_ROOT.iterdir()):
        if child.is_dir() and _read_sml_directory(child):
            entry: dict[str, Any] = {"name": child.name, "path": str(child)}
            remote = _git_remote_of(child)
            if remote:
                entry["gitRepoUrl"], entry["gitBranch"] = remote
            out.append(entry)
    return jsonify(out)


@sml_bp.get("/sml/repos")
def list_attached_repos():
    """Joins AtScale's /wapi/p/repo (repo url/branch) with
    /wapi/p/projects/deployed (repo -> projects -> models) so the Load tab's
    AtScale picker can show real project/model names, not just raw repo
    URLs - both calls already exist in atscale/client.py, this just combines
    them for the frontend."""
    client = get_client()
    if not client:
        return jsonify({"error": "Not authenticated"}), 401

    repos = client.list_repos()
    deployed = {d.get("repoId"): d for d in client.list_deployed_projects()}
    out = []
    for repo in repos:
        entry = deployed.get(repo.get("id"), {})
        out.append(
            {
                "repoId": repo.get("id"),
                "name": repo.get("name"),
                "url": repo.get("url"),
                "branch": repo.get("defaultBranch") or "main",
                "projects": entry.get("projects", []),
            }
        )
    return jsonify(out)


@sml_bp.delete("/sml/repos/<repo_id>")
def unlink_attached_repo(repo_id: str):
    """Unregisters a repo AtScale still lists as attached even though its
    actual Git side is gone (e.g. the GitHub repo was deleted) - lets the
    Load tab's picker clean up a now-broken entry instead of it sitting
    there permanently pointing at nothing."""
    client = get_client()
    if not client:
        return jsonify({"error": "Not authenticated"}), 401
    try:
        client.delete_repo(repo_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 502
    return jsonify({"ok": True})


@sml_bp.post("/sml/import")
def import_files():
    """Import an SML model from an explicit set of {name, body} files - the
    same shape /api/sml/generate returns, so round-tripping (generate, edit
    outside the wizard, re-import) works without a filesystem trip."""
    payload = request.get_json(force=True, silent=True) or {}
    files = payload.get("files")
    if not files:
        return jsonify({"error": "Missing 'files'"}), 400
    file_map = {f["name"]: f["body"] for f in files}
    return jsonify(parse_sml(file_map))


@sml_bp.post("/sml/import-path")
def import_path():
    """Import an SML model from a local directory - this Flask process reads
    the filesystem directly, so `path` must be reachable from wherever the
    API server runs (its own machine, not the browser's)."""
    payload = request.get_json(force=True, silent=True) or {}
    raw_path = payload.get("path")
    if not raw_path:
        return jsonify({"error": "Missing 'path'"}), 400
    root = Path(raw_path).expanduser()
    if not root.is_dir():
        return jsonify({"error": f"'{raw_path}' is not a directory on the API server"}), 400
    files = _read_sml_directory(root)
    if not files:
        return jsonify({"error": f"No .yml/.yaml files found under '{raw_path}'"}), 400
    return jsonify(parse_sml(files))


@sml_bp.post("/sml/import-git")
def import_git():
    """Clone (or pull, if already cached) the Git repo AtScale is linked to,
    then import the SML it contains. Credentials come from connections.yaml's
    `git` connection - see docs/BUILD_PLAN.md / CLAUDE.md for the schema.

    When `modelName` is given (the Load tab's curated repo picker always
    sends it), clones directly into that model's workspace directory instead
    of the anonymous `.data/git-cache/` - the *same* directory Save/Deploy
    use, so a later Deploy commits on top of real cloned history and does a
    normal fast-forward push instead of re-`git init`-ing a disconnected
    history and getting rejected as non-fast-forward."""
    import shutil

    from git import GitCommandError, Repo

    payload = request.get_json(force=True, silent=True) or {}
    config = current_app.config["SML_WIZARD_CONFIG"]
    git_conn = resolve_git_connection(config, payload.get("connectionName", "git"))
    repo_url = payload.get("repoUrl") or git_conn.get("repo")
    branch = payload.get("branch") or git_conn.get("branch") or "main"
    username = git_conn.get("username")
    token = git_conn.get("token")
    model_name = payload.get("modelName")

    if not repo_url:
        return jsonify({"error": "No repo URL - pass 'repoUrl' or set connections.<name>.git.repo"}), 400

    auth_url = repo_url
    if token and repo_url.startswith("https://"):
        auth = f"{username}:{token}" if username else token
        auth_url = repo_url.replace("https://", f"https://{auth}@", 1)

    if model_name:
        repo_dir = model_workspace_dir(model_name)
    else:
        cache_dir = Path(__file__).resolve().parent.parent / ".data" / "git-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        repo_dir = cache_dir / "".join(c if c.isalnum() else "_" for c in repo_url)

    try:
        if (repo_dir / ".git").exists():
            repo = Repo(str(repo_dir))
            repo.remotes.origin.set_url(auth_url)
            repo.remotes.origin.fetch()
            repo.git.checkout(branch)
            repo.remotes.origin.pull()
        else:
            repo = Repo.clone_from(auth_url, str(repo_dir), branch=branch)
        # Workspace/<model> is a visible, user-browsable directory (unlike the
        # old hidden .data/git-cache) - don't leave the token embedded in its
        # .git/config once the operation's done. push_sml_to_repo re-injects
        # it before every push regardless, so this is safe to clear here.
        if model_name:
            repo.remotes.origin.set_url(repo_url)
    except GitCommandError as e:
        if not model_name:
            shutil.rmtree(repo_dir, ignore_errors=True)
        return jsonify({"error": f"Git operation failed: {e}"}), 502

    files = _read_sml_directory(repo_dir)
    if not files:
        return jsonify({"error": f"No .yml/.yaml files found in {repo_url}@{branch}"}), 400
    return jsonify(parse_sml(files))
