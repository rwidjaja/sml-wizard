# SML Wizard — Build Plan

## Context

We're building a new app, **sml-wizard**, that lets a user log into AtScale, pick a
registered data source, drag its tables onto a visual canvas, mark tables as
fact/dimension, wire up joins (including dimension-to-dimension "snowflake" joins,
which is the key differentiator vs. what exists today), configure metrics/hierarchies/
aliases/secondary attributes per column, generate an AtScale SML repository, validate
it, and publish it (Git commit → attach repo to AtScale → deploy → show BI connection
info).

Two source materials ground this:

- **Design handoff** (`/Users/rwidjaja/Downloads/design_handoff_sml_wizard/`): a
  pixel-precise UI spec (`README.md`) plus a working HTML/JS prototype
  (`ModelWizard.dc.html`). The prototype is drag-drop/canvas/inspector reference only —
  its `buildSml()` is fact→dimension only (verified: the `fromFact` ternary has no
  branch for dim-to-dim; a dim-to-dim join today silently emits a mislabeled
  fact-shaped relationship). Its "Push & Deploy" stepper is 100% `setTimeout` mock, no
  real API calls anywhere. Its state also hardcodes hierarchy levels to L1/L2/L3.
- **ps-utils** (`/Users/rwidjaja/Development/Atscale/ps-utils`, TypeScript): has the
  real, tested AtScale REST integration (auth, data-warehouse/repo/deploy endpoints),
  SQL introspection patterns, and an SML→legacy-catalog-XML compiler needed by
  AtScale's git-deploy endpoint. Per your steer, **SML generation itself (datasets,
  hierarchies, measures, model relationships, dim-to-dim joins) is new Python logic**
  driven by the wizard's own explicit user input — not a port of ps-utils' inference
  algorithms (those infer structure from a live DB automatically; our wizard has the
  user declare structure explicitly, which is a different and simpler problem).
  ps-utils is reused for **integration plumbing**: AtScale auth, REST endpoints, and
  the deploy/XML-compile step.
- The **`atscale-sml-model-generator` skill** (bundled with this session) is the
  authoritative rules source for correct SML shape — including explicit rules for
  snowflake-relationship placement (Rule 5, 9, 11), `calculation_method` enums,
  `unrelated_dimensions_handling`, secondary-attribute placement, degenerate
  dimensions, and role-play — plus a Python validator (`scripts/validate_sml.py`) we
  can adapt. This is the rulebook the new Python generator must follow.

Your latest steer, folded in below:
- **Deploy integration**: pull ps-utils in as a **git submodule** (real, updatable
  lineage to the source we're porting from) rather than branching sml-wizard inside
  ps-utils itself or shelling out to it as a subprocess, and reimplement the needed
  pieces (AtScale auth incl. the cookie-based flow, and the SML→catalog-XML compiler)
  in **Python**, since it's all just REST/API calls — the Flask backend should do the
  actual execution itself.
- **Dynamic hierarchies**: the mockup's fixed L1/L2/L3 is wrong. A hierarchy can be as
  short as 2 levels or longer than 8. Levels must be a user-ordered, arbitrary-length
  chain per dimension table, and secondary attributes / level aliases must attach to
  whichever level the user actually built — not a fixed enum of three.

## Repo & stack

Monorepo at `/Users/rwidjaja/Development/Atscale/sml-wizard`:
```
/api    Flask (Python 3.11+), REST JSON, pyyaml for SML emission
/web    React 18 + TypeScript + Vite, zustand for state, native HTML5 DnD, SVG joins
/reference/ps-utils   ps-utils added as a git submodule (real lineage to upstream,
                      `git submodule update --remote` to pull future ps-utils changes)
                      — used as the direct line-by-line translation source for
                      AtScaleRestClientService.ts, SqlService.ts, catalog-xml-builder.ts,
                      sml-serializer.ts; not executed at runtime, not merged into /api or /web.
```
We're keeping sml-wizard as its own repo rather than branching inside ps-utils itself —
ps-utils' entire build/CI (esbuild CLI bundling, generated docs, the VS Code extension,
action.yml) is built around its own TypeScript toolchain, and grafting a Flask/React app
into that history would fight us more than it'd help. A submodule gives the same "real
git connection to ps-utils" benefit without merging the two toolchains.

Vite dev server proxies `/api` to Flask. No page scroll, min-width 1320px, per the
design tokens/spacing/colors in the handoff README (recreated pixel-for-pixel, not
ported from the prototype's raw markup).

## Backend design (`/api`)

### AtScale client (`api/atscale/client.py`)
Ported from `AtScaleRestClientService.ts` + `RestClientService.ts`:
- Auth: Keycloak ROPC (`POST {baseUrl}/auth/realms/{realm}/protocol/openid-connect/token`)
  for normal Bearer/JWT calls; API-token exchange (`POST /v1/token`); and the headless
  cookie-auth flow (`GET /signin` → scrape Keycloak login form action → `POST`
  credentials → follow redirect → capture `auth_session` or
  `__Secure-better-auth.session_token` cookie) required specifically for
  `/wapi/git/deploy/catalog`. Single retry-on-401 via re-authenticate, same as
  `RestClientService.dispatch`.
- Endpoints: `/wapi/p/data-warehouses` (list sources), `/wapi/p/data-sources/conn/{id}/databases/{db}/schemas/{schema}/tables` and `.../tables/{table}/info` (schema tree —
  this is warehouse-agnostic through AtScale itself, so Databricks catalog/schema/table
  works the same way as Postgres/Snowflake database/schema/table, with no direct
  warehouse credentials needed in Flask), `/wapi/p/repo` (list/create — "attach repo"),
  `/wapi/p/projects/deployed` (idempotency check for existing deployments),
  `/wapi/git/deploy/catalog` (the real deploy call).
- Session storage: in-memory dict keyed by Flask session id (or Redis if available),
  holding token + expiry, matching the design doc's `POST /api/session` contract.

### Column profiling / sample values (`api/atscale/profiling.py`)
AtScale's metadata `.../tables/{table}/info` endpoint gives column name/type, not
distinct counts or sample values. ps-utils' `execute-sql-on-connection`/
`execute-query-on-connection` operations show the answer: query *through AtScale's own*
Postgres-compatible SQL analytics port (the one `SqlService.ts` connects to, with the
documented SSL-pre-negotiation workaround and the "never call client.end() and expect
a FIN" close workaround from `ps-utils/CLAUDE.md`). Port `SqlService.connectPostgres`'s
connect/close logic to Python (`psycopg2`/`asyncpg` + raw-socket SSLRequest probe), and
run `SELECT col, COUNT(*) FROM db.schema.table GROUP BY 1 ORDER BY 2 DESC LIMIT 6` plus
a distinct/null-ratio query per column, cached. This queries AtScale's engine, never the
customer warehouse directly — no extra credentials needed beyond the AtScale session.

### SML generation engine (`api/smlgen/`) — new code, rule-driven
Input: the wizard's model state (nodes, joins, per-column config) — see **Dynamic
hierarchy model** below for the revised shape. Output: `{catalog.yml, connections/*.yml,
datasets/*.yml, dimensions/*.yml, metrics/*.yml, models/*.yml}` as an in-memory
`{path: yaml_text}` map, built with `pyyaml` (never hand-built strings).

Encodes the skill's rules directly (each function cites its rule number in a comment,
per the skill's "cite or fail fast" doctrine):
- **Dim-to-dim (snowflake) joins — the core new logic.** For each join, classify by
  both endpoints' roles (fact/dimension), not just one side like the prototype did:
  - fact↔dim → ordinary `models/*.yml` relationship (`from.dataset` = fact,
    `to.dimension`/`to.level` = dim's relevant level).
  - dim↔dim → an **intra-dimension snowflake relationship** emitted inside the
    *child* dimension's own YAML file (`relationships: [{type: snowflake, from:
    {dataset, join_columns}, to: {dimension, level}}]`), per skill Rule 5/9/11 — never
    a second top-level model relationship, and the snowflake target level must appear
    in a `hierarchies[].levels[]` entry (hidden bridge level if it's a surrogate key).
    This requires grouping the wizard's flat node/join graph into logical dimensions
    (a dimension can be backed by >1 joined table) before emitting dimension YAML —
    something the prototype never does (its dimension emission is single-table only).
  - dim↔dim where the "parent" side has no fact relationship of its own (an outrigger
    dangling off another outrigger) → resolved by walking the join graph to each
    dimension's connected fact(s) transitively; unreachable dimension islands are
    flagged as validation errors before generation, not silently dropped.
- Identifier casing per dialect (Rule 1: Snowflake unquoted → UPPERCASE; Postgres/
  Databricks/BigQuery/Redshift → lowercase) applied uniformly to every identifier
  (`table`, columns, `key_columns`, `name_column`, `join_columns`, metric `column`) —
  ps-utils only did this for the dataset table name, so this is genuinely new, not a
  gap-fill port.
- `calculation_method` enum mapping table (Rule 7), `unrelated_dimensions_handling:
  repeat` on every base metric (Rule 3 — ps-utils never emitted this field at all),
  degenerate dimension rules (Rule 12), role-play only within one fact (Rule 21),
  secondary-attribute/alias placement inside `hierarchies[].levels[]` with own-column
  keying (Rule 8).
- Validate-before-generate checks from the design README: every dimension needs ≥1
  level, every secondary/alias needs an existing attach-to level, every dimension
  reaches a fact (see dim-island check above).

### SML validation (`api/smlgen/validate.py`)
Two layers, both before allowing Publish:
1. Adapt the skill's `scripts/validate_sml.py` checks (MDX whitelist n/a here since we
   emit no calculations UI yet, but case/XREF/structural/CONTRACT checks apply) as a
   pure-Python pre-check.
2. Shell out to **`sml-cli`** (the npm package you named) as the authoritative external
   validator: `npx sml-cli validate <dir>` (or whatever its actual subcommand is —
   confirm against its README when we implement this phase), capturing stdout/stderr
   into the API response. This is the one place we still invoke Node tooling, and only
   as an external validator binary, not as application logic.

### Deploy pipeline (`api/atscale/deploy.py`) — ported from ps-utils, not shelled out
Per your steer: port `atscale-create-repo` and `atscale-deploy-catalog`'s logic to
Python, using the `/reference/ps-utils` submodule as the direct translation source:
- `attach_repo()` → `POST /wapi/p/repo` (simple JSON body; idempotent — check
  `list_repos()` first, matching `AtScaleCreateRepoOperation.ts`).
- Git commit/push → plain GitPython against the repo/branch/PAT from config (new code;
  the prototype had nothing here).
- `deploy()` → port `catalog-xml-builder.ts`'s SML→legacy-XML compiler to Python
  (parse every YAML file by `object_type` into catalog/model/dimension/dataset/
  metric/connection maps, infer `conIds` from `connection_id:` lines + the
  connections map, resolve `repoId`, idempotent `projectId` lookup via
  `/wapi/p/projects/deployed`, build the XML, `POST /wapi/git/deploy/catalog` using
  the cookie-auth environment). This is the single largest, highest-risk port in the
  project — flag it as its own phase with its own review pass once
  `/reference/ps-utils/catalog-xml-builder.ts` is actually read (it wasn't in this
  round of research; do that first thing in that phase).
- Poll deployment status via `/wapi/p/projects/deployed`.

### Config / connections
A `connections.yaml`-shaped config (schema reconstructed in research from
ps-utils docblocks) holds AtScale login, warehouse SQL creds (for the profiling
port), and **the Git PAT** so it's available server-side, per your original ask.

## Frontend design (`/web`)

Three-panel shell, canvas, join-dragging, and inspector are recreated per the design
README's token/spacing spec (colors, sizes, chip styles — all specified verbatim
there), using the prototype only as a behavioral reference for interaction sequencing
(pointerdown/pointermove/pointerup patterns, `data-colkey="{nodeId}::{col}"` hit-testing
for join creation — these DnD/join mechanics are fine to reuse, unlike the SML
generation).

### Dynamic hierarchy model (replaces the mockup's fixed L1/L2/L3)
State shape change from the design doc's `ColumnConfig`:
```ts
interface ColumnConfig {
  measure?: boolean; agg?: AggFn; display?: string; query?: string;
  degen?: boolean; degenDisplay?: string; degenQuery?: string;
  dimRole?: 'none' | 'level' | 'secondary' | 'alias';
  levelOrder?: number;        // position in this table's level chain, only when dimRole==='level'
  attachToKey?: string;       // the *column key* (`${nodeId}::${col}`) of the target level,
}                              // only when dimRole==='secondary' | 'alias' — resolved dynamically,
                               // never a fixed 'L1'|'L2'|'L3' enum
```
Derived selectors (`levelsOf(nodeId)`) return the table's levels **sorted by
`levelOrder`, however many there are** (2 to 8+, no clamp) instead of a fixed 3-slot
array. The hierarchy readout's drag handle (already in the design spec) becomes the
actual mechanism for setting `levelOrder` via reorder, not just cosmetic. The
inspector's level-attach `<select>` for secondary/alias is populated from
`levelsOf(parentNodeId)` live — labelled `L{levelOrder} — {displayName}` computed on
the fly — so it always matches whatever chain the user actually built, including
2-level or 9-level hierarchies. Canvas chips (`L1`/`L2`/`L3`) become `L{n}` computed
from `levelOrder`, uncapped.

This is primarily a state-model and inspector-component change (not a backend
constraint) — the skill's SML rules already treat hierarchies as arbitrary-length
`levels:` lists, so the generation engine above needs no fixed-count logic.

### Component breakdown
- `store/modelStore.ts` (zustand): `ModelState` per design doc, with the revised
  `ColumnConfig` above; derived selectors `levelsOf`, `secondariesOf(nodeId, levelKey)`,
  `aliasesOf(nodeId, levelKey)`, `joinedColumnKeys`, `counters`, plus a new
  `dimensionGroups()` selector that unions dim nodes connected by dim↔dim joins into
  logical multi-table dimensions (feeds both the inspector's hierarchy readout across
  joined tables and the backend payload).
- `panels/SourcePanel` (catalogue tree + search + DnD source), `panels/Canvas` (nodes,
  join SVG edges, drag-to-link), `panels/Inspector` (table role / column config /
  dynamic hierarchy readout), `SmlViewerModal` (generated file tabs + publish stepper).
- Persistence: `POST/GET /api/models` (server-side, not localStorage — the prototype
  had no persistence at all, so this is new).
- Publish stepper wired to real endpoints (`/api/publish/git`, `/api/publish/attach`,
  `/api/publish/deploy` + polling) instead of the prototype's `setTimeout` mock.

## Phased delivery

1. Scaffold monorepo (`/api`, `/web`, `/reference/ps-utils` vendored copy), Flask app
   factory, Vite+React+TS shell with the design tokens, zustand store skeleton.
2. AtScale auth + `/api/sources`, `/api/sources/{id}/schemas` (client.py) — source
   picker + catalogue tree working against a real AtScale instance.
3. Canvas: node render/drag/drop-to-add, selection, join-dot drag-to-link with
   `data-colkey` hit-testing, SVG edges (fact-vs-dim-vs-dim-dim edge coloring).
4. Inspector: table role/names, fact metric/degenerate config, **dynamic hierarchy**
   level/secondary/alias config per the revised state model above, hierarchy readout.
5. Column profiling endpoint (AtScale SQL-port connection, ported close/SSL
   workarounds) + sample values in the inspector.
6. SML generation engine (`api/smlgen/`) including dim-to-dim snowflake logic, casing
   rules, validate-before-generate checks; SML viewer modal.
7. SML validation: internal Python pre-checks + `sml-cli` shell-out.
8. Model persistence (`/api/models`).
9. Publish pipeline: read `/reference/ps-utils/catalog-xml-builder.ts` in full, port
   auth (incl. cookie flow) + XML compiler + deploy call to Python; Git commit/push;
   attach-repo; deploy polling; stepper UI wired to real endpoints; final
   Excel/Power BI connection-info panel.

Each phase ends with a working, demoable slice; phase 9 is flagged as the highest-risk,
most novel port and gets read-then-review before implementation, not built blind from
today's research summary alone.

## Verification
- Backend: pytest unit tests per `api/smlgen/` rule (casing, calculation_method
  mapping, dim-to-dim relationship shape, degenerate handling) using small
  hand-built model-state fixtures with known-correct expected YAML, plus the
  `sml-cli`/internal validator run on generated output.
- Frontend: exercise the wizard end-to-end in the browser (`preview_start`/Browser
  pane) against a real or sandbox AtScale instance — drag tables, build a hierarchy of
  varying depth (2 levels and 8+ levels in the same session), create a dim-to-dim
  join, generate SML, confirm the viewer shows correct YAML, run validation.
- Deploy phase: dry-run against a disposable AtScale project/catalog before treating
  the pipeline as done.
