import { useEffect, useState } from 'react'
import RunList from './RunList'

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
  async function handleLogout() {
    await fetch('/api/logout', { method: 'POST' })
    onLoggedOut()
  }

  return (
    <main className="dashboard">
      <header>
        <h1>Signal Deck</h1>
        <button onClick={handleLogout}>Log out</button>
      </header>
      <RunList />
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
