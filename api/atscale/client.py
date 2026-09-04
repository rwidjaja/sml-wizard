"""AtScale REST client.

Ported from reference/ps-utils/src/services/AtScaleRestClientService.ts and
RestClientService.ts. Covers the auth + metadata endpoints needed for Phase 2
(source picker, schema tree). The cookie-auth flow and the git-deploy endpoint
are added in the Phase 9 publish pipeline (api/atscale/deploy.py) — not needed
for browsing data sources.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests


class AtScaleAuthError(RuntimeError):
    pass


class AtScaleApiError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"AtScale API error {status} for {url}: {body}")
        self.status = status
        self.body = body
        self.url = url


@dataclass
class AtScaleEnvironment:
    """Mirrors AtScaleEnvironment / KeycloakEnvironment config in AtScaleRestClientService.ts."""

    base_url: str
    username: str | None = None
    password: str | None = None
    realm: str = "atscale"
    client_id: str = "atscale-ai-link"
    client_secret: str | None = None
    api_token: str | None = None
    auth_type: str = "keycloak"  # "keycloak" | "basic"
    insecure: bool = True
    use_raw_api_token: bool = False

    _token: str | None = field(default=None, init=False, repr=False)
    _token_expires_at: float = field(default=0.0, init=False, repr=False)

    def invalidate(self) -> None:
        self._token = None
        self._token_expires_at = 0.0

    def _keycloak_token_url(self) -> str:
        return f"{self.base_url}/auth/realms/{self.realm}/protocol/openid-connect/token"

    def authenticate(self, force: bool = False) -> tuple[str, dict[str, str]]:
        """Returns (scheme, headers) - either bearer or basic auth headers."""
        if not force and self._token and time.time() < self._token_expires_at:
            return "bearer", {"Authorization": f"Bearer {self._token}"}

        if self.api_token and self.use_raw_api_token:
            self._token = self.api_token
            self._token_expires_at = time.time() + 3600
            return "bearer", {"Authorization": f"Bearer {self._token}"}

        if self.api_token:
            token = self._exchange_api_token()
            self._token = token
            self._token_expires_at = time.time() + 3600
            return "bearer", {"Authorization": f"Bearer {self._token}"}

        if self.auth_type == "basic":
            return "basic", {}

        token = self._keycloak_password_grant()
        self._token = token
        self._token_expires_at = time.time() + 3600
        return "bearer", {"Authorization": f"Bearer {self._token}"}

    def _exchange_api_token(self) -> str:
        resp = requests.post(
            f"{self.base_url}/v1/token",
            headers={"Authorization": f"Bearer {self.api_token}"},
            json={},
            verify=not self.insecure,
        )
        if resp.status_code >= 300:
            raise AtScaleAuthError(f"API token exchange failed: {resp.status_code} {resp.text}")
        return resp.json()["accessToken"]

    def _keycloak_password_grant(self) -> str:
        form = {
            "client_id": self.client_id,
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
            "scope": "openid",
        }
        if self.client_secret:
            form["client_secret"] = self.client_secret
        resp = requests.post(self._keycloak_token_url(), data=form, verify=not self.insecure)
        if resp.status_code >= 300:
            raise AtScaleAuthError(f"Keycloak auth failed: {resp.status_code} {resp.text}")
        return resp.json()["access_token"]


class AtScaleClient:
    """Dispatches requests against AtScale's /wapi/p/ REST API with retry-on-401,
    mirroring RestClientService.dispatch()."""

    def __init__(self, env: AtScaleEnvironment):
        self.env = env

    def _dispatch(self, method: str, path: str, is_retry: bool = False, **kwargs: Any) -> requests.Response:
        scheme, headers = self.env.authenticate(force=is_retry)
        auth = None
        if scheme == "basic":
            auth = (self.env.username or "", self.env.password or "")
        req_headers = {**kwargs.pop("headers", {}), **headers}
        url = f"{self.env.base_url}{path}"
        resp = requests.request(
            method,
            url,
            headers=req_headers,
            auth=auth,
            verify=not self.env.insecure,
            **kwargs,
        )
        if resp.status_code == 401 and not is_retry:
            self.env.invalidate()
            return self._dispatch(method, path, is_retry=True, **kwargs)
        if resp.status_code >= 300:
            raise AtScaleApiError(resp.status_code, resp.text, url)
        return resp

    # -- data sources / connections -------------------------------------------------
    def list_data_sources(self) -> list[dict[str, Any]]:
        return self._dispatch("GET", "/wapi/p/data-warehouses").json()

    # -- schema tree (warehouse-agnostic through AtScale's own metadata API) --------
    # NOTE: `connection_id` here is the data-warehouse's `connectionId` field (a
    # name-based string, e.g. "PostgresDB") - confirmed against a real instance.
    # The warehouse's own `id` (a UUID) and the inner `connections[].id` both 404 /
    # 500 ("ConnectionGroup ... not found") on this path family.
    def list_databases(self, connection_id: str) -> list[str]:
        path = f"/wapi/p/data-sources/conn/{connection_id}/databases"
        return self._dispatch("GET", path).json()

    def list_schemas(self, connection_id: str, database: str) -> list[str]:
        path = f"/wapi/p/data-sources/conn/{connection_id}/databases/{database}/schemas"
        return self._dispatch("GET", path).json()

    def list_tables(self, connection_id: str, database: str, schema: str) -> list[str]:
        # Confirmed shape: a plain list of table-name strings, not objects.
        path = f"/wapi/p/data-sources/conn/{connection_id}/databases/{database}/schemas/{schema}/tables"
        return self._dispatch("GET", path).json()

    def get_table_info(self, connection_id: str, database: str, schema: str, table: str) -> dict[str, Any]:
        path = (
            f"/wapi/p/data-sources/conn/{connection_id}/databases/{database}"
            f"/schemas/{schema}/tables/{table}/info"
        )
        return self._dispatch("GET", path).json()

    # -- repos (git attach) ----------------------------------------------------------
    def list_repos(self) -> list[dict[str, Any]]:
        return self._dispatch("GET", "/wapi/p/repo").json()

    def create_repo(
        self, name: str, url: str, repo_type: str = "catalog",
        visible_branches_pattern: str | None = None, default_branch: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "url": url, "type": repo_type}
        if visible_branches_pattern:
            body["visibleBranchesPattern"] = visible_branches_pattern
        if default_branch:
            body["defaultBranch"] = default_branch
        return self._dispatch("POST", "/wapi/p/repo", json=body).json()

    # -- deployments -------------------------------------------------------------------
    def list_deployed_projects(self) -> list[dict[str, Any]]:
        return self._dispatch("GET", "/wapi/p/projects/deployed").json()
