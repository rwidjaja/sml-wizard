import { useState } from 'react'
import { DeployFailure, SmlValidationFailure, validateSml, type DeploySteps, type SmlFile } from '../api/client'

interface Props {
  files: SmlFile[]
  onClose: () => void
  onDeploy: () => Promise<{ ok: boolean; steps: DeploySteps }>
}

const STEP_LABELS: [keyof DeploySteps, string][] = [
  ['generate', 'Generate SML'],
  ['save', 'Save to disk'],
  ['git', 'Commit & push to Git'],
  ['attach', 'Attach repo to AtScale'],
  ['deploy', 'Deploy catalog'],
]

export function SmlViewerModal({ files, onClose, onDeploy }: Props) {
  const [activeIdx, setActiveIdx] = useState(0)
  const [validating, setValidating] = useState(false)
  const [validation, setValidation] = useState<{ passed: boolean; output: string } | null>(null)

  const [deploying, setDeploying] = useState(false)
  const [deploySteps, setDeploySteps] = useState<DeploySteps | null>(null)
  const [deployError, setDeployError] = useState<string | null>(null)

  async function runValidate() {
    setValidating(true)
    setValidation(null)
    try {
      const result = await validateSml(files)
      setValidation(result)
    } catch (err) {
      setValidation({ passed: false, output: err instanceof Error ? err.message : String(err) })
    } finally {
      setValidating(false)
    }
  }

  async function runDeploy() {
    setDeploying(true)
    setDeployError(null)
    setDeploySteps(null)
    try {
      const { steps } = await onDeploy()
      setDeploySteps(steps)
    } catch (err) {
      if (err instanceof DeployFailure) {
        setDeployError(err.message)
        setDeploySteps(err.steps)
      } else if (err instanceof SmlValidationFailure) {
        setDeployError(err.errors.join('\n'))
      } else {
        setDeployError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setDeploying(false)
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="sml-modal" onClick={(e) => e.stopPropagation()}>
        <div className="sml-modal-header">
          <div>
            <div className="eyebrow" style={{ color: 'var(--as-join)' }}>
              GENERATED SML
            </div>
            <div className="identity-title">{files.length} files</div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-ghost" onClick={runValidate} disabled={validating}>
              {validating ? 'Validating…' : 'Validate with sml-cli'}
            </button>
            <button className="btn btn-primary" onClick={runDeploy} disabled={deploying}>
              {deploying ? 'Deploying…' : 'Deploy'}
            </button>
            <button className="btn btn-ghost" onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        {validation && (
          <div className={`validation-banner ${validation.passed ? 'validation-pass' : 'validation-fail'}`}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              {validation.passed ? 'Validation passed' : 'Validation failed'}
            </div>
            <pre className="validation-output">{validation.output}</pre>
          </div>
        )}

        {(deploySteps || deployError) && (
          <div className={`validation-banner ${deployError ? 'validation-fail' : 'validation-pass'}`}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{deployError ? 'Deploy failed' : 'Deployed'}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {STEP_LABELS.map(([key, label]) => {
                const done = !!deploySteps?.[key]
                return (
                  <div key={key} style={{ display: 'flex', gap: 8, fontSize: 12 }}>
                    <span style={{ color: done ? '#3bd44a' : 'var(--as-muted-30)' }}>{done ? '✓' : '·'}</span>
                    <span>{label}</span>
                  </div>
                )
              })}
            </div>
            {deploySteps?.git && (
              <div className="field-note" style={{ marginTop: 8 }}>
                {deploySteps.git.repoUrl} @ {deploySteps.git.branch} ({deploySteps.git.commit.slice(0, 7)})
              </div>
            )}
            {deployError && <pre className="validation-output">{deployError}</pre>}
          </div>
        )}

        <div className="sml-modal-body">
          <div className="sml-file-tabs">
            {files.map((f, i) => (
              <div
                key={f.name}
                className={`sml-file-tab ${i === activeIdx ? 'sml-file-tab-active' : ''}`}
                onClick={() => setActiveIdx(i)}
              >
                {f.name}
              </div>
            ))}
          </div>
          <pre className="sml-file-content">{files[activeIdx]?.body}</pre>
        </div>
      </div>
    </div>
  )
}
