"""POST /api/sml/generate, POST /api/sml/validate, POST /api/sml/import*."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from config import resolve_git_connection
from smlgen.build import ValidationError, build_sml
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
    root.mkdir(parents=True, exist_ok=True)
    for f in files:
        p = root / f["name"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f["body"], encoding="utf-8")
    return jsonify({"ok": True, "path": str(root), "count": len(files)})


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
    `git` connection - see docs/BUILD_PLAN.md / CLAUDE.md for the schema."""
    import shutil

    from git import GitCommandError, Repo

    payload = request.get_json(force=True, silent=True) or {}
    config = current_app.config["SML_WIZARD_CONFIG"]
    git_conn = resolve_git_connection(config, payload.get("connectionName", "git"))
    repo_url = payload.get("repoUrl") or git_conn.get("repo")
    branch = payload.get("branch") or git_conn.get("branch") or "main"
    username = git_conn.get("username")
    token = git_conn.get("token")

    if not repo_url:
        return jsonify({"error": "No repo URL - pass 'repoUrl' or set connections.<name>.git.repo"}), 400

    auth_url = repo_url
    if token and repo_url.startswith("https://"):
        auth = f"{username}:{token}" if username else token
        auth_url = repo_url.replace("https://", f"https://{auth}@", 1)

    cache_dir = Path(__file__).resolve().parent.parent / ".data" / "git-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = cache_dir / "".join(c if c.isalnum() else "_" for c in repo_url)

    try:
        if repo_dir.exists():
            repo = Repo(str(repo_dir))
            repo.remotes.origin.set_url(auth_url)
            repo.remotes.origin.fetch()
            repo.git.checkout(branch)
            repo.remotes.origin.pull()
        else:
            Repo.clone_from(auth_url, str(repo_dir), branch=branch)
    except GitCommandError as e:
        shutil.rmtree(repo_dir, ignore_errors=True)
        return jsonify({"error": f"Git operation failed: {e}"}), 502

    files = _read_sml_directory(repo_dir)
    if not files:
        return jsonify({"error": f"No .yml/.yaml files found in {repo_url}@{branch}"}), 400
    return jsonify(parse_sml(files))
