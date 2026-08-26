import { useEffect, useState } from 'react'

const PROJECTS = ['rustle', 'ticktrader'] as const
type Project = (typeof PROJECTS)[number]

export default function ConfigRoots({ onBack }: { onBack: () => void }) {
  const [project, setProject] = useState<Project>('rustle')
  const [roots, setRoots] = useState<string[]>([])
  const [newRoot, setNewRoot] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [configs, setConfigs] = useState<string[] | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch(`/api/config-roots/${project}`)
      .then((res) => (res.ok ? res.json() : { roots: [] }))
      .then((data) => {
        if (!cancelled) setRoots(data.roots)
      })
    return () => {
      cancelled = true
    }
  }, [project])

  function selectProject(p: Project) {
    setProject(p)
    setConfigs(null)
    setError(null)
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const res = await fetch(`/api/config-roots/${project}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root: newRoot }),
    })
    if (res.ok) {
      const data = await res.json()
      setRoots(data.roots)
      setNewRoot('')
    } else {
      const body = await res.json().catch(() => null)
      setError(body?.detail ?? 'Could not add directory')
    }
  }

  async function handleScan() {
    const res = await fetch(`/api/config-scan/${project}`)
    if (res.ok) setConfigs((await res.json()).configs)
  }

  return (
    <div className="config-roots">
      <button className="back-button" onClick={onBack}>
        ← Back
      </button>
      <h2>Config roots</h2>
      <div className="run-tabs">
        {PROJECTS.map((p) => (
          <button
            key={p}
            className={`run-tab${project === p ? ' run-tab--active' : ''}`}
            onClick={() => selectProject(p)}
          >
            {p}
          </button>
        ))}
      </div>
      <ul className="config-roots-list">
        {roots.length === 0 && <li className="overview-empty">No config roots configured.</li>}
        {roots.map((root) => (
          <li key={root}>{root}</li>
        ))}
      </ul>
      <form onSubmit={handleAdd} className="config-roots-form">
        <input
          value={newRoot}
          onChange={(e) => setNewRoot(e.target.value)}
          placeholder="/absolute/path/to/configs"
        />
        <button type="submit" disabled={!newRoot}>
          Add root
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      <button onClick={handleScan}>Scan for configs</button>
      {configs !== null && (
        <ul className="config-roots-list">
          {configs.length === 0 && <li className="overview-empty">No .toml configs found.</li>}
          {configs.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
