import { useState } from 'react'
import { useModelStore, counters } from './store/modelStore'
import { useSessionStore } from './store/sessionStore'
import { SourcePanel } from './panels/SourcePanel'
import { Canvas } from './panels/Canvas'
import { Inspector } from './panels/Inspector'
import { LoginScreen } from './panels/LoginScreen'
import { SmlViewerModal } from './panels/SmlViewerModal'
import { ManageModelModal } from './panels/ManageModelModal'
import { CalculationsModal } from './panels/CalculationsModal'
import { PreviewTab } from './panels/PreviewTab'
import { deployModel, generateSml, SmlValidationFailure, type GenerateSmlPayload, type SmlFile } from './api/client'
import { MODEL_NAME_HINT, slugifyModelName } from './lib/naming'
import './App.styles.css'

type AppTab = 'build' | 'preview'

export default function App() {
  const state = useModelStore()
  const c = counters(state)
  const authenticated = useSessionStore((s) => s.authenticated)
  const [tab, setTab] = useState<AppTab>('build')
  const [files, setFiles] = useState<SmlFile[] | null>(null)
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)
  const [showManage, setShowManage] = useState(false)
  const [showCalculations, setShowCalculations] = useState(false)

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
      calculations: state.calculations,
      // Redeploying a model loaded from AtScale - push back to the same
      // repo/branch it came from (git_ops.ensure_github_repo's slug-derived
      // name lookup would otherwise risk creating an unrelated repo, since
      // AtScale's own repo display name is allowed to contain spaces).
      gitRepoUrl: state.sourceRepo?.url,
      gitBranch: state.sourceRepo?.branch,
    }
  }

  // "Deploy" previews the generated SML first (and lets you validate it with
  // sml-cli) before committing to the real pipeline - the SmlViewerModal's own
  // Deploy button (wired to handleDeploy below) runs generate -> save -> git
  // commit/push -> attach to AtScale -> deploy.
  async function handleGenerate() {
    if (!state.modelName.trim()) {
      setGenError('Enter a model name (top left) before generating SML.')
      return
    }
    const payload = buildPayload(state.modelName)
    if (!payload) return

    setGenerating(true)
    setGenError(null)
    try {
      const result = await generateSml(payload)
      setFiles(result.files)
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

  // Rebuilds the payload from *current* state rather than reusing whatever
  // was generated earlier - any tables/joins/metrics/calculations added or
  // removed since the preview was opened are what actually gets deployed.
  async function handleDeploy() {
    const payload = buildPayload(state.modelName)
    if (!payload) throw new Error(genError ?? 'Cannot deploy - fix the error above first')
    return deployModel(payload)
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-left">
          <div className="eyebrow">ATSCALE · SML WIZARD</div>
          <input
            className="model-name-input"
            placeholder="Model name"
            title={MODEL_NAME_HINT}
            value={state.modelName}
            onChange={(e) => state.setModelName(slugifyModelName(e.target.value))}
          />
          <div className="app-tabs">
            {(['build', 'preview'] as AppTab[]).map((t) => (
              <button
                key={t}
                className={`app-tab ${tab === t ? 'app-tab-active' : ''}`}
                onClick={() => setTab(t)}
              >
                {t === 'build' ? 'Build' : 'Preview'}
              </button>
            ))}
          </div>
        </div>
        {tab === 'build' && (
          <div className="app-header-right">
            <span className="counter">{c.datasets} DATASETS</span>
            <span className="counter">{c.joins} JOINS</span>
            <span className="counter">{c.metrics} METRICS</span>
            <span className="counter">{c.levels} LEVELS</span>
            <span className="counter">{c.calculations} CALCULATIONS</span>
            <button className="btn btn-ghost" onClick={() => setShowCalculations(true)}>
              Calculations
            </button>
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
        )}
      </header>
      {tab === 'build' && genError && (
        <div className="login-error" style={{ padding: '8px 28px', whiteSpace: 'pre-wrap' }}>
          {genError}
        </div>
      )}
      {tab === 'build' && (
        <div className="app-body">
          <SourcePanel />
          <Canvas />
          <Inspector />
        </div>
      )}
      {tab === 'preview' && <PreviewTab />}
      {files && <SmlViewerModal files={files} onClose={() => setFiles(null)} onDeploy={handleDeploy} />}
      {showManage && <ManageModelModal onClose={() => setShowManage(false)} />}
      {showCalculations && <CalculationsModal onClose={() => setShowCalculations(false)} />}
    </div>
  )
}
