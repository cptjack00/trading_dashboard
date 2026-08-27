import { useEffect, useState } from 'react'

export type RunStatus = 'live' | 'stopped' | 'crashed' | 'backtest'

export type Run = {
  run_id: string
  project: string
  run_type: string
  status: RunStatus
  started_at: number
  ended_at: number | null
  pnl: number
}

const POLL_MS = 5000

export function useRuns(): Run[] {
  const [runs, setRuns] = useState<Run[]>([])

  useEffect(() => {
    let cancelled = false
    async function load() {
      const res = await fetch('/api/runs')
      if (!res.ok || cancelled) return
      const data: Run[] = await res.json()
      if (!cancelled) setRuns(data)
    }
    load()
    const poll = setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(poll)
    }
  }, [])

  return runs
}

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return [h, m, s].map((n) => String(n).padStart(2, '0')).join(':')
}

function RunTicket({
  run,
  now,
  active,
  onSelect,
  selected,
  showCheckbox,
}: {
  run: Run
  now: number
  active?: boolean
  onSelect: (run: Run) => void
  selected?: boolean
  showCheckbox?: boolean
}) {
  const end = run.ended_at ?? now
  const duration = end - run.started_at

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect(run)
    }
  }

  return (
    <li
      className={`run-ticket channel-${run.project}${active ? ' active' : ''}${selected ? ' selected' : ''}`}
      onClick={() => onSelect(run)}
      onKeyDown={handleKeyDown}
      role="button"
      tabIndex={0}
    >
      {showCheckbox && (
        <span className={`ticket-check${selected ? ' checked' : ''}`} aria-hidden="true">
          {selected ? '✓' : ''}
        </span>
      )}
      <span className={`ticket-status ${run.status}`} aria-hidden="true" />
      <span className="ticket-main">
        <span className="ticket-name">{run.run_id}</span>
        <span className="ticket-meta">
          <span>{run.project}</span>
          <span>{run.status === 'backtest' ? 'BACKTEST' : formatDuration(duration)}</span>
        </span>
      </span>
      <span className={`ticket-pnl ${run.pnl > 0 ? 'pos' : run.pnl < 0 ? 'neg' : 'flat'}`}>
        {run.pnl >= 0 ? '+' : ''}
        {run.pnl.toFixed(2)}
      </span>
    </li>
  )
}

export default function RunList({
  runs,
  activeKey,
  onSelectRun,
  compareMode = false,
  selectedKeys,
  onToggleCompare,
}: {
  runs: Run[]
  activeKey?: string
  onSelectRun: (run: Run) => void
  compareMode?: boolean
  selectedKeys?: Set<string>
  onToggleCompare?: (run: Run) => void
}) {
  const [now, setNow] = useState(() => Date.now() / 1000)

  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(tick)
  }, [])

  if (runs.length === 0) {
    return <p className="overview-empty">No runs yet.</p>
  }

  return (
    <ul className="run-list">
      {runs.map((run) => {
        const key = `${run.project}-${run.run_id}`
        return (
          <RunTicket
            key={key}
            run={run}
            now={now}
            active={activeKey === key}
            onSelect={compareMode ? onToggleCompare! : onSelectRun}
            selected={compareMode && (selectedKeys?.has(key) ?? false)}
            showCheckbox={compareMode}
          />
        )
      })}
    </ul>
  )
}
