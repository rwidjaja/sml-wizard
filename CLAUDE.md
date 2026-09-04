# CLAUDE.md — sml-wizard project conventions

See [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) for the full approved build plan
(context, architecture, phased delivery, verification). Read it before making
architectural changes — it captures decisions already made with the user, not just
a proposal.

## Project in one paragraph

A three-panel web wizard for building an AtScale SML (Semantic Modeling Language)
model visually: pick a data source, drag tables onto a canvas, mark fact/dimension,
join tables (including dimension-to-dimension "snowflake" joins), configure
metrics/hierarchies/aliases/secondary attributes, generate + validate SML, then
publish (Git commit → attach repo to AtScale → deploy → show BI connection info).

## Repo layout

- `/api` — Flask (Python), REST JSON backend. Venv lives outside the repo at
  `/Users/rwidjaja/Development/venv/sml-wizard` (see `.venv` marker file at repo
  root — do not recreate `.venv/` inside this repo).
- `/web` — React 18 + TypeScript + Vite frontend.
- `/reference/ps-utils` — **git submodule** pointing at
  `https://github.com/AtScaleInc/ps-utils.git`. Read-only reference for porting:
  `AtScaleRestClientService.ts` (auth + REST endpoints), `SqlService.ts` (warehouse
  connect/profiling patterns), `catalog-xml-builder.ts` (SML→legacy-XML compiler
  needed by the deploy endpoint), `sml-serializer.ts`. **Never executed at runtime,
  never edited** — it's a submodule; if it needs updating, `git submodule update
  --remote reference/ps-utils` from upstream, don't hand-edit files inside it.
- `docs/BUILD_PLAN.md` — the full plan (see above).

## Key architectural decisions (don't relitigate without cause)

1. **SML generation is new Python logic** (`api/smlgen/`), not a port of ps-utils'
   inference algorithms. ps-utils infers structure from a live DB; this wizard has
   the user declare structure explicitly via drag-drop — different, simpler problem.
   The `atscale-sml-model-generator` skill's rules (Rule 1, 3, 5, 7, 8, 9, 11, 12, 21
   especially) are the authoritative source for correct SML shape, including
   dim-to-dim/snowflake relationship placement — cite the rule number in code
   comments when implementing a rule.
2. **Dynamic hierarchies, not fixed L1/L2/L3.** A hierarchy can be 2 levels or 8+.
   `ColumnConfig.dimRole` is `'level' | 'secondary' | 'alias'` (not `'L1'|'L2'|'L3'`),
   ordered via `levelOrder`, with `attachToKey` pointing at another column's key —
   never a fixed enum. See BUILD_PLAN.md's "Dynamic hierarchy model" section.
3. **Deploy pipeline is ported to Python, not shelled out to Node.** Auth (including
   the cookie-based flow needed by `/wapi/git/deploy/catalog`) and the catalog-XML
   compiler get reimplemented in `api/atscale/`, translating directly from the
   submodule. This is flagged as the highest-risk phase (9) — read
   `reference/ps-utils/src/algorithm/catalog-xml-builder.ts` in full before touching
   it, since it wasn't read in the research that produced this plan.
4. **Warehouse browsing goes through AtScale's own metadata REST API**
   (`/wapi/p/data-sources/conn/{id}/databases/{db}/schemas/{schema}/tables[/...]`),
   never a direct SQLAlchemy connection to the customer's warehouse from Flask. This
   is what makes Databricks catalog/schema/table, Snowflake database/schema/table,
   and Postgres database/schema/table all work through one code path with no
   warehouse credentials in Flask.
5. **Column profiling/sample values** go through AtScale's own Postgres-compatible
   SQL analytics port (same one `SqlService.ts` in ps-utils targets), not the
   metadata API (which has no distinct-count/null-ratio/sample-value data) and not
   the customer warehouse directly.

## Gotchas ported from ps-utils/CLAUDE.md (apply here too)

- Closing a `psycopg2`/pg connection to AtScale's SQL-analytics port: the proxy may
  never send a TCP FIN after a Terminate message. Race the close against a ~1s
  timeout and force-close the socket after, same pattern as `SqlService.close()`.
- AtScale's SQL-analytics port may respond to a plaintext startup with an SSL
  negotiation demand even when real SSL isn't wanted — see `SqlService.ts`'s
  `preNegotiateSslPlaintext` for the raw-socket SSLRequest workaround to port.
- `calculation_method` is an exact enumerated string (`sum`, `average`, `minimum`,
  `maximum`, `count distinct`, `count non-null`, `sum distinct`, ...) — never
  `avg`/`min`/`max`/`count_distinct`.
