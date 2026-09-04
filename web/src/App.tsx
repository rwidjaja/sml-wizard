import { useModelStore, counters } from './store/modelStore'
import { useSessionStore } from './store/sessionStore'
import { SourcePanel } from './panels/SourcePanel'
import { Canvas } from './panels/Canvas'
import { Inspector } from './panels/Inspector'
import { LoginScreen } from './panels/LoginScreen'
import './App.styles.css'

export default function App() {
  const state = useModelStore()
  const c = counters(state)
  const authenticated = useSessionStore((s) => s.authenticated)

  if (!authenticated) {
    return <LoginScreen />
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
          <button className="btn btn-ghost" onClick={() => state.reset()}>
            Reset
          </button>
          <button className="btn btn-primary">Generate SML</button>
        </div>
      </header>
      <div className="app-body">
        <SourcePanel />
        <Canvas />
        <Inspector />
      </div>
    </div>
  )
}
