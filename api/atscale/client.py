"""AtScale REST client.

Ported from reference/ps-utils/src/services/AtScaleRestClientService.ts and
RestClientService.ts, including the cookie-auth flow required specifically by
`/wapi/git/deploy/catalog` (see AtScaleEnvironment._acquire_session_cookie -
faithfully ported from acquireSessionCookie() in the TS source, a headless
Keycloak authorization-code flow: GET /signin, scrape the login form action,
POST credentials, follow the redirect, capture the session cookie).
"""

from __future__ import annotations

import re
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
    session_cookie: str | None = None
    #: When True, authenticate() acquires a Design Center session cookie
    #: instead of a Bearer JWT - required by /wapi/git/deploy/catalog.
    cookie_auth: bool = False

    _token: str | None = field(default=None, init=False, repr=False)
    _token_expires_at: float = field(default=0.0, init=False, repr=False)
    _cookie_header: str | None = field(default=None, init=False, repr=False)

    def invalidate(self) -> None:
        self._token = None
        self._token_expires_at = 0.0
        self._cookie_header = None

    def _keycloak_token_url(self) -> str:
        return f"{self.base_url}/auth/realms/{self.realm}/protocol/openid-connect/token"

    def authenticate(self, force: bool = False) -> tuple[str, dict[str, str]]:
        """Returns (scheme, headers) - bearer, basic, or cookie auth headers."""
        if self.cookie_auth:
            return self._authenticate_cookie(force)

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

    def _authenticate_cookie(self, force: bool) -> tuple[str, dict[str, str]]:
        if not force and self._cookie_header:
            return "cookie", {"Cookie": self._cookie_header}

        if self.session_cookie:
            self._cookie_header = f"auth_session={self.session_cookie}"
            return "cookie", {"Cookie": self._cookie_header}

        # SSO environments (no username) fall back to an exchanged JWT - the
        # Design Center metadata endpoints accept that in addition to a cookie.
        if self.api_token and not self.username:
            token = self._exchange_api_token()
            self._token = token
            self._token_expires_at = time.time() + 3600
            return "bearer", {"Authorization": f"Bearer {self._token}"}

        self._cookie_header = self._acquire_session_cookie()
        return "cookie", {"Cookie": self._cookie_header}

    def _acquire_session_cookie(self) -> str:
        """Headless Keycloak authorization-code flow (ported verbatim from
        acquireSessionCookie() in AtScaleRestClientService.ts):
          1. GET /signin -> state cookie + Keycloak redirect URL
          2. GET <Keycloak login page> -> scrape the login form's action URL
          3. POST username/password to that URL -> 302 to /signin/callback?code=...
          4. GET the callback URL -> Set-Cookie: auth_session=... (or better-auth's
             __Secure-better-auth.session_token on newer AtScale builds)
        """
        if not self.username or not self.password:
            raise AtScaleAuthError(
                "Deploying requires Keycloak credentials to acquire the Design Center "
                "session cookie automatically. Add 'username' and 'password' to the "
                "atscale: block in connections.yaml."
            )

        session = requests.Session()
        session.verify = not self.insecure

        r1 = session.get(f"{self.base_url}/signin", allow_redirects=False)
        kc_url = r1.headers.get("Location", "")
        if not kc_url:
            raise AtScaleAuthError(
                f"{self.base_url}/signin did not redirect to Keycloak (status {r1.status_code}). "
                "Verify the AtScale URL is correct."
            )

        r2 = session.get(kc_url, allow_redirects=False)
        match = re.search(r'["\'`](https?:[^"\'`]+login-actions/authenticate[^"\'`]+)[`"\']', r2.text)
        if not match:
            raise AtScaleAuthError(
                "Could not extract the Keycloak login form action URL from the login page "
                "- the Keycloak theme may have changed."
            )
        form_action_url = match.group(1)

        r3 = session.post(
            form_action_url,
            data={"username": self.username, "password": self.password},
            allow_redirects=False,
        )
        if not (300 <= r3.status_code < 400):
            raise AtScaleAuthError(
                f"Keycloak login failed (status {r3.status_code}). "
                "Check username/password in connections.yaml's atscale: block."
            )
        raw_location = r3.headers.get("Location", "")
        callback_url = raw_location if raw_location.startswith("http") else f"{self.base_url}{raw_location}"

        session.get(callback_url, allow_redirects=False)

        cookie_names = ("auth_session", "__Secure-better-auth.session_token")
        if not any(c in session.cookies for c in cookie_names):
            raise AtScaleAuthError(
                "/signin/callback did not set a session cookie (expected auth_session or "
                "__Secure-better-auth.session_token). The Keycloak code exchange may have failed."
            )
        return "; ".join(f"{c.name}={c.value}" for c in session.cookies)

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

    def deploy_repo(
        self,
        repo_id: str,
        sml_raw_files: list[dict[str, str]],
        project_xml: str,
        project_name: str,
        con_ids: list[str],
        project_id: str,
        tableau_servers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """POST /wapi/git/deploy/catalog - requires `self.env.cookie_auth = True`
        (a separate AtScaleEnvironment/AtScaleClient from the one used for
        /wapi/p/* Bearer-JWT calls, per ps-utils' dual-env design)."""
        body = {
            "repoId": repo_id,
            "projectId": project_id,
            "projectName": project_name,
            "conIds": con_ids,
            "smlRawFiles": sml_raw_files,
            "projectXml": project_xml,
            "cubes": [],
            "tableauServers": tableau_servers or [],
            "perspectives": [],
        }
        resp = self._dispatch("POST", "/wapi/git/deploy/catalog", json=body)
        return resp.json() if resp.text else {}
