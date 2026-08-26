import { useEffect, useState } from 'react'
import ConfigRoots from './ConfigRoots'
import NewRun from './NewRun'
import RunComparison from './RunComparison'
import RunList, { type Run } from './RunList'
import RunOverview from './RunOverview'

function runKey(run: Run): string {
  return `${run.project}-${run.run_id}`
}

type AuthState = 'checking' | 'authenticated' | 'anonymous'

async function checkSession(): Promise<boolean> {
  const res = await fetch('/api/session')
  return res.ok
}

function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    })
    setSubmitting(false)
    if (res.ok) {
      onLoggedIn()
    } else {
      setError('Incorrect password')
    }
  }

  return (
    <main className="login">
      <form onSubmit={handleSubmit}>
        <h1>Signal Deck</h1>
        <label htmlFor="password">Shared secret</label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
        />
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={submitting || !password}>
          Log in
        </button>
      </form>
    </main>
  )
}

function DashboardShell({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [selectedRun, setSelectedRun] = useState<Run | null>(null)
  const [compareMode, setCompareMode] = useState(false)
  const [compareSelection, setCompareSelection] = useState<Run[]>([])
  const [comparing, setComparing] = useState<Run[] | null>(null)
  const [showConfigRoots, setShowConfigRoots] = useState(false)
  const [showNewRun, setShowNewRun] = useState(false)

  function toggleCompareSelection(run: Run) {
    setCompareSelection((prev) => {
      if (prev.some((r) => runKey(r) === runKey(run))) return prev.filter((r) => runKey(r) !== runKey(run))
      if (prev.length >= 4) return prev
      return [...prev, run]
    })
  }

  function exitCompareMode() {
    setCompareMode(false)
    setCompareSelection([])
  }

  async function handleLogout() {
    await fetch('/api/logout', { method: 'POST' })
    onLoggedOut()
  }

  return (
    <main className="dashboard">
      <header>
        <h1>Signal Deck</h1>
        <div>
          <button onClick={() => setShowNewRun(true)}>New run</button>
          <button onClick={() => setShowConfigRoots(true)}>Config roots</button>
          <button onClick={handleLogout}>Log out</button>
        </div>
      </header>
      {showNewRun ? (
        <NewRun onBack={() => setShowNewRun(false)} onStarted={() => setShowNewRun(false)} />
      ) : showConfigRoots ? (
        <ConfigRoots onBack={() => setShowConfigRoots(false)} />
      ) : comparing ? (
        <RunComparison
          runs={comparing}
          onBack={() => {
            setComparing(null)
            exitCompareMode()
          }}
        />
      ) : selectedRun ? (
        <RunOverview run={selectedRun} onBack={() => setSelectedRun(null)} />
      ) : (
        <>
          <div className="compare-bar">
            {compareMode ? (
              <>
                <span>{compareSelection.length} of 2–4 runs selected</span>
                <button disabled={compareSelection.length < 2} onClick={() => setComparing(compareSelection)}>
                  Compare
                </button>
                <button onClick={exitCompareMode}>Cancel</button>
              </>
            ) : (
              <button onClick={() => setCompareMode(true)}>Compare runs</button>
            )}
          </div>
          <RunList
            onSelectRun={setSelectedRun}
            compareMode={compareMode}
            selectedKeys={new Set(compareSelection.map(runKey))}
            onToggleCompare={toggleCompareSelection}
          />
        </>
      )}
    </main>
  )
}

export default function App() {
  const [auth, setAuth] = useState<AuthState>('checking')

  useEffect(() => {
    checkSession().then((ok) => setAuth(ok ? 'authenticated' : 'anonymous'))
  }, [])

  if (auth === 'checking') return null
  if (auth === 'anonymous') {
    return <Login onLoggedIn={() => setAuth('authenticated')} />
  }
  return <DashboardShell onLoggedOut={() => setAuth('anonymous')} />
}
