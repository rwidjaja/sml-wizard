// Phase 4 fleshes this out: table role/names, fact metric/degenerate config,
// dynamic hierarchy level/secondary/alias config (see modelStore.ts's
// levelsOf/secondariesOf/aliasesOf — arbitrary-length chain, not fixed L1/L2/L3),
// hierarchy readout.
export function Inspector() {
  return (
    <aside className="panel-right">
      <div className="section-label">03 — Column Inspector</div>
      <div style={{ color: 'var(--as-muted)' }}>No selection — Phase 4.</div>
    </aside>
  )
}
