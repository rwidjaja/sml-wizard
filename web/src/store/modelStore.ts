import { create } from 'zustand'

// State shape per docs/BUILD_PLAN.md "Dynamic hierarchy model" — replaces the
// design mockup's fixed L1/L2/L3 with an arbitrary-length, user-ordered chain.

export type Role = 'fact' | 'dimension' | null
export type AggFn = 'SUM' | 'MIN' | 'MAX' | 'COUNT' | 'COUNT DISTINCT' | 'AVG'
export type DimRole = 'none' | 'level' | 'secondary' | 'alias'

export interface ColumnMeta {
  name: string
  type: string
}

export interface TableRef {
  schema: string
  name: string
  columns: ColumnMeta[]
}

export interface Node {
  id: string
  schema: string
  table: string
  columns: ColumnMeta[]
  x: number
  y: number
  role: Role
  dimName?: string
  hierName?: string
  factName?: string
}

export interface Join {
  id: string
  a: { node: string; column: string }
  b: { node: string; column: string }
}

/** Column key format: `${nodeId}::${column}` — matches the design doc's data-colkey. */
export type ColumnKey = string

export interface ColumnConfig {
  measure?: boolean
  agg?: AggFn
  display?: string
  query?: string

  degen?: boolean
  degenDisplay?: string
  degenQuery?: string

  dimRole?: DimRole
  /** Position in this table's level chain, set only when dimRole === 'level'.
   *  Arbitrary length (2 to 8+) — never clamped to a fixed L1/L2/L3 enum. */
  levelOrder?: number
  /** The column key of the target level, set only when dimRole is 'secondary' | 'alias'.
   *  Resolved dynamically against whatever chain the user actually built. */
  attachToKey?: ColumnKey
}

export interface Selection {
  node: string
  column: string | null
}

export interface LinkDrag {
  from: { node: string; column: string }
  side: 'l' | 'r'
  to: { x: number; y: number }
}

export interface ModelState {
  sourceId: string | null
  search: string
  openSchemas: Record<string, boolean>
  nodes: Node[]
  joins: Join[]
  cfg: Record<ColumnKey, ColumnConfig>
  selection: Selection | null
  linkDrag: LinkDrag | null

  setSourceId: (id: string | null) => void
  setSearch: (search: string) => void
  toggleSchema: (schema: string) => void
  addNode: (schema: string, table: string, x: number, y: number, columns?: ColumnMeta[]) => string
  moveNode: (id: string, x: number, y: number) => void
  removeNode: (id: string) => void
  setNodeRole: (id: string, role: Role) => void
  setNodeField: (id: string, field: 'dimName' | 'hierName' | 'factName', value: string) => void
  setLevelOrder: (nodeId: string, key: ColumnKey, direction: 'up' | 'down') => void
  addJoin: (a: { node: string; column: string }, b: { node: string; column: string }) => void
  removeJoin: (id: string) => void
  select: (selection: Selection | null) => void
  setColumnConfig: (key: ColumnKey, patch: Partial<ColumnConfig>) => void
  setColumnDimRole: (nodeId: string, key: ColumnKey, dimRole: DimRole) => void
  reset: () => void
}

const columnKey = (nodeId: string, column: string): ColumnKey => `${nodeId}::${column}`

/** Auto-seeded names for a role, derived from the table name (strip dim_/fact_/fct_
 *  prefix, title-case). Shared by addNode's initial role guess and setNodeRole's
 *  manual role change so both paths seed dimName/hierName/factName consistently. */
function seededNamesFor(table: string, role: Role) {
  const stem = table.replace(/^(dim_|fact_|fct_)/i, '')
  const titled = stem.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  return {
    dimName: role === 'dimension' ? `${titled} Dimension` : undefined,
    hierName: role === 'dimension' ? `${titled} Hierarchy` : undefined,
    factName: role === 'fact' ? `${titled} Facts` : undefined,
  }
}

let seq = 0

export const useModelStore = create<ModelState>((set, get) => ({
  sourceId: null,
  search: '',
  openSchemas: {},
  nodes: [],
  joins: [],
  cfg: {},
  selection: null,
  linkDrag: null,

  setSourceId: (id) => set({ sourceId: id, nodes: [], joins: [], cfg: {}, selection: null }),
  setSearch: (search) => set({ search }),
  toggleSchema: (schema) =>
    set((s) => ({ openSchemas: { ...s.openSchemas, [schema]: !s.openSchemas[schema] } })),

  addNode: (schema, table, x, y, columns = []) => {
    const id = `n${seq++}`
    const role: Role = /^(fct|fact)/i.test(table) ? 'fact' : /^dim/i.test(table) ? 'dimension' : null
    const names = seededNamesFor(table, role)
    set((s) => ({
      nodes: [...s.nodes, { id, schema, table, columns, x: Math.max(0, x), y: Math.max(0, y), role, ...names }],
      selection: { node: id, column: null },
    }))
    return id
  },

  moveNode: (id, x, y) =>
    set((s) => ({
      nodes: s.nodes.map((n) => (n.id === id ? { ...n, x: Math.max(0, x), y: Math.max(0, y) } : n)),
    })),

  removeNode: (id) =>
    set((s) => {
      const cfg = Object.fromEntries(Object.entries(s.cfg).filter(([key]) => !key.startsWith(`${id}::`)))
      return {
        nodes: s.nodes.filter((n) => n.id !== id),
        joins: s.joins.filter((j) => j.a.node !== id && j.b.node !== id),
        cfg,
        selection: s.selection?.node === id ? null : s.selection,
      }
    }),

  setNodeRole: (id, role) =>
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== id) return n
        const seeded = seededNamesFor(n.table, role)
        return {
          ...n,
          role,
          dimName: n.dimName || seeded.dimName,
          hierName: n.hierName || seeded.hierName,
          factName: n.factName || seeded.factName,
        }
      }),
    })),

  setNodeField: (id, field, value) =>
    set((s) => ({ nodes: s.nodes.map((n) => (n.id === id ? { ...n, [field]: value } : n)) })),

  setColumnDimRole: (nodeId, key, dimRole) =>
    set((s) => {
      const existing = s.cfg[key] ?? {}
      let levelOrder = existing.levelOrder
      if (dimRole === 'level' && existing.dimRole !== 'level') {
        const currentLevels = levelsOf(s, nodeId)
        levelOrder = currentLevels.length ? currentLevels[currentLevels.length - 1].levelOrder + 1 : 0
      }
      return { cfg: { ...s.cfg, [key]: { ...existing, dimRole, levelOrder } } }
    }),

  setLevelOrder: (nodeId, key, direction) =>
    set((s) => {
      const levels = levelsOf(s, nodeId)
      const idx = levels.findIndex((l) => l.key === key)
      const swapIdx = direction === 'up' ? idx - 1 : idx + 1
      if (idx < 0 || swapIdx < 0 || swapIdx >= levels.length) return {}
      const a = levels[idx]
      const b = levels[swapIdx]
      return {
        cfg: {
          ...s.cfg,
          [a.key]: { ...s.cfg[a.key], levelOrder: b.levelOrder },
          [b.key]: { ...s.cfg[b.key], levelOrder: a.levelOrder },
        },
      }
    }),

  addJoin: (a, b) => {
    if (a.node === b.node) return
    const exists = get().joins.some(
      (j) =>
        (j.a.node === a.node && j.a.column === a.column && j.b.node === b.node && j.b.column === b.column) ||
        (j.b.node === a.node && j.b.column === a.column && j.a.node === b.node && j.a.column === b.column),
    )
    if (exists) return
    set((s) => ({ joins: [...s.joins, { id: `j${seq++}`, a, b }] }))
  },

  removeJoin: (id) => set((s) => ({ joins: s.joins.filter((j) => j.id !== id) })),

  select: (selection) => set({ selection }),

  setColumnConfig: (key, patch) =>
    set((s) => ({ cfg: { ...s.cfg, [key]: { ...s.cfg[key], ...patch } } })),

  reset: () => set({ nodes: [], joins: [], cfg: {}, selection: null, linkDrag: null }),
}))

// ---- Derived selectors -----------------------------------------------------

/** Levels of a dimension table, sorted by levelOrder — arbitrary length (2 to 8+),
 *  never clamped. This is the core fix over the mockup's fixed L1/L2/L3. */
export function levelsOf(state: ModelState, nodeId: string) {
  return Object.entries(state.cfg)
    .filter(([key, c]) => key.startsWith(`${nodeId}::`) && c.dimRole === 'level')
    .map(([key, c]) => ({ key, column: key.split('::')[1], levelOrder: c.levelOrder ?? 0, config: c }))
    .sort((x, y) => x.levelOrder - y.levelOrder)
}

export function secondariesOf(state: ModelState, levelKey: ColumnKey) {
  return Object.entries(state.cfg)
    .filter(([, c]) => c.dimRole === 'secondary' && c.attachToKey === levelKey)
    .map(([key, c]) => ({ key, config: c }))
}

export function aliasesOf(state: ModelState, levelKey: ColumnKey) {
  return Object.entries(state.cfg)
    .filter(([, c]) => c.dimRole === 'alias' && c.attachToKey === levelKey)
    .map(([key, c]) => ({ key, config: c }))
}

export function joinedColumnKeys(state: ModelState): Set<ColumnKey> {
  const set = new Set<ColumnKey>()
  for (const j of state.joins) {
    set.add(columnKey(j.a.node, j.a.column))
    set.add(columnKey(j.b.node, j.b.column))
  }
  return set
}

/** Unions dimension nodes connected by dim<->dim joins into logical multi-table
 *  dimensions, for the inspector's cross-table hierarchy readout and the SML
 *  generation payload. A dim<->dim join is one where neither/both endpoints'
 *  parent node has role 'fact'. */
export function dimensionGroups(state: ModelState): string[][] {
  const dimIds = new Set(state.nodes.filter((n) => n.role === 'dimension').map((n) => n.id))
  const adjacency = new Map<string, Set<string>>()
  for (const id of dimIds) adjacency.set(id, new Set())
  for (const j of state.joins) {
    if (dimIds.has(j.a.node) && dimIds.has(j.b.node)) {
      adjacency.get(j.a.node)!.add(j.b.node)
      adjacency.get(j.b.node)!.add(j.a.node)
    }
  }
  const seen = new Set<string>()
  const groups: string[][] = []
  for (const id of dimIds) {
    if (seen.has(id)) continue
    const group: string[] = []
    const stack = [id]
    while (stack.length) {
      const cur = stack.pop()!
      if (seen.has(cur)) continue
      seen.add(cur)
      group.push(cur)
      for (const next of adjacency.get(cur) ?? []) if (!seen.has(next)) stack.push(next)
    }
    groups.push(group)
  }
  return groups
}

export function counters(state: ModelState) {
  const datasets = state.nodes.length
  const joins = state.joins.length
  const metrics = Object.values(state.cfg).filter((c) => c.measure).length
  const levels = Object.values(state.cfg).filter((c) => c.dimRole === 'level').length
  return { datasets, joins, metrics, levels }
}

export { columnKey }
