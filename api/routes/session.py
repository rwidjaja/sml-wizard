"""POST /api/session — authenticate against AtScale, hold the token server-side.

Matches the design handoff's contract: the browser never sees the AtScale
password/token, only an app session cookie. Two ways to authenticate:
  - `{"connectionName": "..."}` — resolve url/user/pass from connections.yaml
  - `{"url", "username", "password", ...}` — login-form fields typed directly in the UI
"""

from __future__ import annotations

import uuid

from flask import Blueprint, current_app, jsonify, request, session

from atscale.client import AtScaleClient, AtScaleAuthError, AtScaleEnvironment
from config import resolve_atscale_connection

session_bp = Blueprint("session", __name__)

# In-memory store: app-session-id -> AtScaleClient. Swap for Redis if this needs to
# survive a process restart or run behind multiple workers.
_CLIENTS: dict[str, AtScaleClient] = {}


def get_client() -> AtScaleClient | None:
    sid = session.get("sml_wizard_sid")
    if not sid:
        return None
    return _CLIENTS.get(sid)


@session_bp.post("/session")
def create_session():
    body = request.get_json(force=True, silent=True) or {}
    config = current_app.config["SML_WIZARD_CONFIG"]

    if "connectionName" in body:
        conn = resolve_atscale_connection(config, body["connectionName"])
        if not conn:
            return jsonify({"error": f"Unknown connection '{body['connectionName']}'"}), 400
        env = AtScaleEnvironment(
            base_url=conn.get("url", "").rstrip("/"),
            username=conn.get("username"),
            password=conn.get("password"),
            realm=conn.get("realm", "atscale"),
            client_id=conn.get("clientId", "atscale-ai-link"),
            client_secret=conn.get("clientSecret"),
            api_token=conn.get("apiToken"),
            auth_type=conn.get("authType", "keycloak"),
            insecure=conn.get("insecure", True),
        )
    else:
        required = {"url"}
        missing = required - body.keys()
        if missing:
            return jsonify({"error": f"Missing fields: {sorted(missing)}"}), 400
        env = AtScaleEnvironment(
            base_url=body["url"].rstrip("/"),
            username=body.get("username"),
            password=body.get("password"),
            realm=body.get("realm", "atscale"),
            api_token=body.get("apiToken") or body.get("secret"),
            insecure=body.get("insecure", True),
        )

    client = AtScaleClient(env)
    try:
        _, expires_at = _authenticate_and_expiry(client)
    except AtScaleAuthError as e:
        return jsonify({"error": str(e)}), 401

    sid = str(uuid.uuid4())
    _CLIENTS[sid] = client
    session["sml_wizard_sid"] = sid
    return jsonify({"ok": True, "expiresAt": expires_at})


def _authenticate_and_expiry(client: AtScaleClient) -> tuple[str, float]:
    scheme, _ = client.env.authenticate()
    return scheme, client.env._token_expires_at


@session_bp.delete("/session")
def destroy_session():
    sid = session.pop("sml_wizard_sid", None)
    if sid:
        _CLIENTS.pop(sid, None)
    return jsonify({"ok": True})
