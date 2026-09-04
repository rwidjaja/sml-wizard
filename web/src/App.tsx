import { useState } from 'react'
import { useModelStore, counters } from './store/modelStore'
import { useSessionStore } from './store/sessionStore'
import { SourcePanel } from './panels/SourcePanel'
import { Canvas } from './panels/Canvas'
import { Inspector } from './panels/Inspector'
import { LoginScreen } from './panels/LoginScreen'
import { SmlViewerModal } from './panels/SmlViewerModal'
import { ManageModelModal } from './panels/ManageModelModal'
import { deployModel, generateSml, SmlValidationFailure, type GenerateSmlPayload, type SmlFile } from './api/client'
import './App.styles.css'

export default function App() {
  const state = useModelStore()
  const c = counters(state)
  const authenticated = useSessionStore((s) => s.authenticated)
  const [files, setFiles] = useState<SmlFile[] | null>(null)
  const [lastPayload, setLastPayload] = useState<GenerateSmlPayload | null>(null)
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)
  const [showManage, setShowManage] = useState(false)

  if (!authenticated) {
    return <LoginScreen />
  }

  function buildPayload(modelName: string): GenerateSmlPayload | null {
    if (!state.sourceMeta) {
      setGenError('Select a data source before generating SML.')
      return null
    }
    const schema = state.nodes[0]?.schema
    if (!schema) {
      setGenError('Add at least one table to the canvas before generating SML.')
      return null
    }
    return {
      modelName,
      connectionName: `con_${state.sourceMeta.database}_${schema}`,
      asConnection: state.sourceMeta.connectionId,
      database: state.sourceMeta.database,
      schema,
      dialect: state.sourceMeta.dialect,
      nodes: state.nodes,
      joins: state.joins,
      cfg: state.cfg,
    }
  }

  // "Deploy" previews the generated SML first (and lets you validate it with
  // sml-cli) before committing to the real pipeline - the SmlViewerModal's own
  // Deploy button (wired to handleDeploy below) runs generate -> save -> git
  // commit/push -> attach to AtScale -> deploy.
  async function handleGenerate() {
    const modelName = window.prompt('Model name:', 'my_model')
    if (!modelName) return
    const payload = buildPayload(modelName)
    if (!payload) return

    setGenerating(true)
    setGenError(null)
    try {
      const result = await generateSml(payload)
      setFiles(result.files)
      setLastPayload(payload)
    } catch (err) {
      if (err instanceof SmlValidationFailure) {
        setGenError(err.errors.join('\n'))
      } else {
        setGenError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setGenerating(false)
    }
  }

  async function handleDeploy() {
    if (!lastPayload) throw new Error('No model to deploy')
    return deployModel(lastPayload)
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-left">
          <div className="eyebrow">ATSCALE · SML WIZARD</div>
          <h1 className="headline">
            Define the model, <em>generate</em> the SML
          </h1>
        </div>
        <div className="app-header-right">
          <span className="counter">{c.datasets} DATASETS</span>
          <span className="counter">{c.joins} JOINS</span>
          <span className="counter">{c.metrics} METRICS</span>
          <span className="counter">{c.levels} LEVELS</span>
          <button className="btn btn-ghost" onClick={() => setShowManage(true)}>
            Save / Load
          </button>
          <button className="btn btn-ghost" onClick={() => state.reset()}>
            Reset
          </button>
          <button className="btn btn-primary" onClick={handleGenerate} disabled={generating}>
            {generating ? 'Generating…' : 'Deploy'}
          </button>
        </div>
      </header>
      {genError && (
        <div className="login-error" style={{ padding: '8px 28px', whiteSpace: 'pre-wrap' }}>
          {genError}
        </div>
      )}
      <div className="app-body">
        <SourcePanel />
        <Canvas />
        <Inspector />
      </div>
      {files && <SmlViewerModal files={files} onClose={() => setFiles(null)} onDeploy={handleDeploy} />}
      {showManage && <ManageModelModal onClose={() => setShowManage(false)} />}
    </div>
  )
}
