# SML Wizard

A visual tool for **quickly standing up a first AtScale semantic model**: log
into AtScale, browse a registered data source, drag tables onto a canvas,
mark them as facts or dimensions, wire up joins, configure metrics and
hierarchies, then generate, validate, and deploy the resulting SML (Semantic
Modeling Language) — all the way from a blank canvas to a live, queryable
catalog.

**Scope.** This is a quick-start tool, not a replacement for AtScale's own
modeler. It's aimed at getting a straightforward star/snowflake model — the
kind that covers most day-one modeling — up and deployed fast, with the
tedious YAML hand-authoring done for you. It deliberately doesn't try to
cover every modeling pattern the full AtScale product supports (see [Known
limitations](#known-limitations) below); reach for the real modeler for
those, or hand-edit the generated SML afterward.

See [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) for the original architecture
plan and [CLAUDE.md](CLAUDE.md) for the key decisions and conventions behind
it.

## Stack

- **`/api`** — Flask (Python 3.11+), REST JSON backend.
- **`/web`** — React 18 + TypeScript + Vite frontend.
- **`/reference/ps-utils`** — a git submodule of
  [AtScaleInc/ps-utils](https://github.com/AtScaleInc/ps-utils), kept as a
  read-only line-by-line porting reference for the AtScale REST client, the
  legacy catalog XML compiler, and related patterns. Not executed at runtime.

## What it can do

**Connect**
- Log into AtScale with a URL/username/password/API-token form, or skip the
  form entirely with a saved connection from `connections.yaml`.
- Browse any data source AtScale has registered — Postgres, Snowflake,
  Databricks, etc. — through AtScale's own metadata API, so the app never
  needs direct warehouse credentials. Schema/table/column browsing, search,
  and per-column sample-adjacent metadata all go through this one path.

**Model visually**
- Drag tables from the catalogue tree onto the canvas; role (fact/dimension)
  is guessed from the table name and always user-editable.
- Draw joins by dragging from one column's connector dot to another.
  Fact↔dimension and dimension↔dimension ("snowflake") joins are both
  supported and rendered distinctly (orange when a fact is involved, muted
  gray otherwise). Clicking a fact-involved join opens a role-play prompt
  (e.g. "Order", "Ship" for two date FKs on one fact joined to the same
  conformed date dimension); deleting a join is a separate, explicit action
  (a small ✕ badge at the join's midpoint) so you can't lose a join by
  accident while trying to label it.
- Build hierarchies of **arbitrary depth** (2 levels or 8+, not a fixed
  L1/L2/L3) by marking any number of columns as "Hierarchy level" on a
  dimension table, and reorder them with the hierarchy readout's up/down
  controls.
- Add secondary attributes and level aliases, attached to whichever level you
  actually built (the attach-to-level list is always generated from the
  current hierarchy, never a fixed set of choices).
- Per level/secondary/alias, independently override the **key column**
  (join/identity), **value column** (what's shown to users), and **query
  name** (the SML `unique_name`) when they differ from the column you
  clicked — e.g. mark `year` as the level, key it on `datekey`, and show
  `year_name` to users. All three default to the clicked column if left
  alone.
- Mark a dimension as a time dimension (emits SML `type: time` with
  `time_unit` on each level).
- On a fact table, create metrics (aggregate function + display/query name)
  or degenerate dimensions (expose a fact column as an attribute with no
  separate dimension table) per column.

**Generate & validate**
- "Generate SML" builds the full repository (catalog, connections, datasets,
  dimensions, metrics, model) from the canvas model and shows it in a
  tabbed viewer, with a validate-before-generate pass (every dimension needs
  a hierarchy, every dimension must reach a fact, etc.).
- Validate the generated SML with the real `sml-cli` tool (shelled out to,
  not reimplemented) before deploying.
- The generation rules (identifier casing per warehouse dialect, exact
  `calculation_method` enum values, degenerate-dimension handling, snowflake
  relationship placement, role-play propagation, and more) were built from
  the `atscale-sml-model-generator` skill's rulebook and then corrected
  against two real, hand-built SML repos pulled from a live AtScale
  instance — see `api/smlgen/build.py`'s module docstring for the specific
  points where the real repos disagreed with the initial assumptions.

**Save, load, and deploy — all just SML**
- There's no separate proprietary save format. "Save" generates the SML and
  writes it to a directory; "Load" imports an existing SML repository —
  from a local directory, or by pulling the Git repo configured in
  `connections.yaml` — and reconstructs the canvas model from it (nodes,
  joins, roles, hierarchies, secondary attributes, key/value/query
  overrides, role-play, time dimensions).
- "Deploy" runs the full pipeline in one click: generate → save to disk →
  create (or reuse) the model's own GitHub repo and push the SML to it →
  attach that repo to AtScale → compile the legacy catalog XML AtScale's
  deploy endpoint requires and deploy it. Verified end-to-end against a
  real AtScale instance and GitHub, including idempotent re-deploy (an
  unchanged model reuses the existing project instead of creating a
  duplicate).

## Known limitations

- **A dimension backed by more than one physical table, joined together
  into a single hierarchy** (SML's `type: snowflake` intra-dimension
  pattern — e.g. an AtScale-built Geography dimension spanning separate
  City/State/Country/Zip tables) isn't authorable from the canvas, which
  models one physical table per node. Importing a repo that uses this
  pattern succeeds, but only the levels backed by the dimension's own
  representative table come in; the rest are skipped rather than
  mis-imported.
- **A dimension with more than one hierarchy** collapses onto this wizard's
  one-hierarchy-per-node model on import; the extra hierarchy isn't
  imported (its relationships, if they resolve to the same underlying
  column, are deduped rather than shown as confusing duplicates).
- **A level alias and an ordinary secondary attribute** are structurally
  identical in emitted SML unless you compare key columns; import currently
  treats every `secondary_attributes` entry as a plain secondary attribute
  rather than detecting a true alias.
- **A composite key** (`key_columns` with more than one column) collapses
  to its first column when round-tripped through the canvas's one-key-
  column-per-level model.
- **No calculated metrics (MDX) authoring.** The wizard covers base metrics,
  degenerate dimensions, and standard/time dimensions; SML `metric_calc`
  objects and MDX expressions aren't modeled in the UI.
- **Deploy's Git step targets GitHub specifically** (repo creation and push
  use the GitHub REST API and a personal access token); other Git hosts
  aren't wired up.
- **Deploying requires real Keycloak username/password credentials** in
  `connections.yaml` (not just an API token) — the `/wapi/git/deploy/catalog`
  endpoint needs a Design Center session cookie, acquired via a headless
  login flow that only works with a real username/password, not SSO.
- **Single-user, single-process session state.** Login sessions and the
  schema-browsing cache live in the Flask process's memory; there's no
  multi-worker or multi-user session store.

## Getting started

1. **Backend**: create/activate a Python 3.11+ virtualenv, then
   `pip install -r api/requirements.txt`. Copy your AtScale/Git connection
   details into `api/connections.yaml` (gitignored — never commit real
   credentials) — see `api/config.py`'s module docstring for the schema.
   Run with `python api/app.py` (serves on `:5000`).
2. **Frontend**: `cd web && npm install && npm run dev` (serves on `:5173`,
   proxying `/api` to the Flask backend).
3. **Validation**: `npx sml-cli` needs to be reachable (via `npx`) for the
   "Validate with sml-cli" and Deploy steps.
4. **Tests**: `cd api && pytest tests/` runs the generator/parser/validator
   test suite.
