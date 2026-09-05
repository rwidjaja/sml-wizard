import { useEffect, useMemo, useState } from 'react'
import { fetchSchemas, fetchSources, type SchemaEntry, type SourceSummary } from '../api/client'
import { useModelStore } from '../store/modelStore'

export function SourcePanel() {
  const [sources, setSources] = useState<SourceSummary[]>([])
  const [schemas, setSchemas] = useState<SchemaEntry[]>([])
  const [loadingSchemas, setLoadingSchemas] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const sourceId = useModelStore((s) => s.sourceId)
  const setSourceId = useModelStore((s) => s.setSourceId)
  const search = useModelStore((s) => s.search)
  const setSearch = useModelStore((s) => s.setSearch)
  const openSchemas = useModelStore((s) => s.openSchemas)
  const toggleSchema = useModelStore((s) => s.toggleSchema)
  const addNode = useModelStore((s) => s.addNode)
  const nodes = useModelStore((s) => s.nodes)
  // useMemo, not an inline selector - a selector returning `new Set(...)` gives
  // useSyncExternalStore a different reference every render and loops.
  const placedTables = useMemo(() => new Set(nodes.map((n) => `${n.schema}.${n.table}`)), [nodes])

  useEffect(() => {
    fetchSources()
      .then(setSources)
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (!sourceId) {
      setSchemas([])
      return
    }
    setLoadingSchemas(true)
    setError(null)
    fetchSchemas(sourceId, search || undefined)
      .then(setSchemas)
      .catch((e) => setError(e.message))
      .finally(() => setLoadingSchemas(false))
  }, [sourceId, search])

  const selectedSource = sources.find((s) => s.id === sourceId)

  function handleDragStart(e: React.DragEvent, schema: string, table: SchemaEntry['tables'][number]) {
    e.dataTransfer.effectAllowed = 'copy'
    e.dataTransfer.setData(
      'application/x-sml-table',
      JSON.stringify({ schema, table: table.name, columns: table.columns }),
    )
  }

  function handleAdd(schema: string, table: SchemaEntry['tables'][number]) {
    addNode(schema, table.name, 40 + Math.random() * 60, 40 + Math.random() * 60, table.columns)
  }

  return (
    <aside className="panel-left">
      <div className="section-label">Data Source</div>
      <select
        className="source-select"
        value={sourceId ?? ''}
        onChange={(e) => {
          const id = e.target.value || null
          const src = sources.find((s) => s.id === id)
          setSourceId(
            id,
            src ? { dialect: src.dialect, connectionId: src.connectionId, database: src.database } : null,
          )
        }}
      >
        <option value="">Select a data source…</option>
        {sources.map((s) => (
          <option key={s.id} value={s.id}>
            {s.label}
          </option>
        ))}
      </select>
      {selectedSource && (
        <div className="source-meta">
          {selectedSource.dialect?.toUpperCase()} · {selectedSource.database}
        </div>
      )}

      {sourceId && (
        <input
          className="source-search"
          placeholder="Search tables"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      )}

      {error && <div className="login-error">{error}</div>}
      {loadingSchemas && <div style={{ color: 'var(--as-muted)' }}>Loading…</div>}

      {schemas.map((schema) => (
        <div key={schema.name}>
          <div className="schema-row" onClick={() => toggleSchema(schema.name)}>
            <span className="schema-glyph">
              <span />
              <span />
              <span />
            </span>
            {schema.name} <span className="schema-meta">(Schema)</span>
            <span className="schema-count">{schema.tables.length}</span>
          </div>
          {openSchemas[schema.name] &&
            schema.tables.map((t) => {
              const isFact = /^(fct|fact)/i.test(t.name)
              const placed = placedTables.has(`${schema.name}.${t.name}`)
              return (
                <div
                  key={t.name}
                  className="table-row"
                  style={placed ? { opacity: 0.5 } : undefined}
                  draggable
                  onDragStart={(e) => handleDragStart(e, schema.name, t)}
                  onDoubleClick={() => handleAdd(schema.name, t)}
                >
                  <span
                    className="table-swatch"
                    style={{ background: isFact ? 'var(--as-fact)' : 'var(--as-dimension)' }}
                  />
                  <span className="table-name">{t.name}</span>
                  <span className="table-cols">{t.columns.length} cols</span>
                </div>
              )
            })}
        </div>
      ))}
    </aside>
  )
}
