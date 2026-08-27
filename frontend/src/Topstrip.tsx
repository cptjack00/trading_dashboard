import type { Run } from './RunList'

const PROJECTS = ['rustle', 'ticktrader'] as const
const PROJECT_LABEL: Record<string, string> = { rustle: 'RUSTLE', ticktrader: 'TICKTRADER-PARA' }

export default function Topstrip({ runs }: { runs: Run[] }) {
  return (
    <header className="topstrip">
      {PROJECTS.map((project) => {
        const projectRuns = runs.filter((r) => r.project === project)
        const live = projectRuns.some((r) => r.status === 'live')
        const pnl = projectRuns.reduce((sum, r) => sum + r.pnl, 0)
        return (
          <div className="channel" key={project}>
            <span className={`pulse-dot${live ? ' live' : ''}`} aria-hidden="true" />
            <div className="channel-body">
              <span className={`channel-name ${project}`}>{PROJECT_LABEL[project]}</span>
              <span className="channel-meta">
                {projectRuns.length} run{projectRuns.length === 1 ? '' : 's'}
                <span className={`channel-pnl ${pnl >= 0 ? 'pos' : 'neg'}`}>
                  {pnl >= 0 ? '+' : ''}
                  {pnl.toFixed(2)}
                </span>
              </span>
            </div>
          </div>
        )
      })}
    </header>
  )
}
