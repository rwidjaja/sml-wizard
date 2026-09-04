import { useState } from 'react'
import { login } from '../api/client'
import { useSessionStore } from '../store/sessionStore'

export function LoginScreen() {
  const [url, setUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [apiToken, setApiToken] = useState('')
  const [busy, setBusy] = useState(false)
  const { error, setError, setAuthenticated } = useSessionStore()

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { expiresAt } = await login({
        url,
        username: username || undefined,
        password: password || undefined,
        apiToken: apiToken || undefined,
        insecure: true,
      })
      setAuthenticated(expiresAt)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="eyebrow">ATSCALE · SML WIZARD</div>
        <h1 className="headline">
          Connect to <em>AtScale</em>
        </h1>
        <label>
          Hostname
          <input
            type="text"
            placeholder="https://your-instance.atscaledomain.com"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
          />
        </label>
        <label>
          Username
          <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label>
          API token (optional, preferred)
          <input type="password" value={apiToken} onChange={(e) => setApiToken(e.target.value)} />
        </label>
        {error && <div className="login-error">{error}</div>}
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? 'Connecting…' : 'Connect'}
        </button>
      </form>
    </div>
  )
}
