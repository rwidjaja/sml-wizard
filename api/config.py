"""Loads the connections.yaml-shaped config described in the build plan.

Schema (reconstructed from ps-utils docblocks — see
reference/ps-utils/src/services/atscale-env.ts and the AtScale*Operation.ts files):

    users:
      admin:
        username: admin
        password: secret

    connections:
      my_atscale:
        atscale:
          url: https://atscale.example.com
          user: admin
          realm: atscale
          insecure: true
      git:
        git:
          username: rwidjaja
          email: rwidjaja@example.com
          token: ghp_...           # personal access token, used as the Basic auth password
          repo: https://github.com/org/repo.git
          branch: main
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from smlgen.naming import slugify_model_name

DEFAULT_CONFIG_PATH = os.environ.get("SML_WIZARD_CONNECTIONS_FILE", "connections.yaml")

# Repo root (this file lives at <repo>/api/config.py), matching where
# start.sh/`connections.yaml` already expect to be run from - not api/'s own
# directory, so the workspace sits next to CLAUDE.md/docs/, not buried in api/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get("SML_WIZARD_WORKSPACE", _REPO_ROOT / "workspace")).resolve()


def model_workspace_dir(model_name: str) -> Path:
    """The one persistent working directory for a given model - both Save and
    Deploy write here, so they operate on the same on-disk checkout instead of
    two different staging locations."""
    root = WORKSPACE_ROOT / slugify_model_name(model_name)
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_config(path: str | None = None) -> dict[str, Any]:
    config_path = Path(path or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        return {"users": {}, "connections": {}}
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("users", {})
    data.setdefault("connections", {})
    return data


def resolve_user(config: dict[str, Any], user_key: str | None) -> dict[str, Any]:
    if not user_key:
        return {}
    return config.get("users", {}).get(user_key, {})


def resolve_atscale_connection(config: dict[str, Any], connection_name: str) -> dict[str, Any]:
    conn = config.get("connections", {}).get(connection_name, {})
    atscale = dict(conn.get("atscale", {}))
    user_key = atscale.get("user")
    if user_key:
        user = resolve_user(config, user_key)
        atscale.setdefault("username", user.get("username"))
        atscale.setdefault("password", user.get("password"))
        atscale.setdefault("apiToken", user.get("apiToken"))
    return atscale


def resolve_git_connection(config: dict[str, Any], connection_name: str) -> dict[str, Any]:
    conn = config.get("connections", {}).get(connection_name, {})
    return dict(conn.get("git", {}))
