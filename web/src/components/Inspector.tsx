import type { FileInfo, Report } from '../api'
import { fileName, plural } from '../util'
import { CloseIcon, Rich, Sev } from './bits'

interface Props {
  report: Report
  file: FileInfo
  onSelect: (path: string) => void
  onOpenCode: (path: string, line?: number) => void
  onOpenIssue: (id: string) => void
  onClose: () => void
  floating?: boolean
  impactOn?: boolean
  onToggleImpact?: () => void
}

function FileList({ paths, onSelect, empty }: {
  paths: string[]
  onSelect: (path: string) => void
  empty: string
}) {
  if (paths.length === 0) return <p className="none">{empty}</p>
  return (
    <ul>
      {paths.map((path) => (
        <li key={path}>
          <button className="link-btn" onClick={() => onSelect(path)} title={path}>
            {fileName(path)}
          </button>
        </li>
      ))}
    </ul>
  )
}

export function Inspector({ report, file, onSelect, onOpenCode, onOpenIssue, onClose, floating,
  impactOn, onToggleImpact }: Props) {
  const issues = report.insights.filter((issue) => file.insights.includes(issue.id))
  const hot = [...file.function_metrics]
    .sort((a, b) => b.complexity - a.complexity || b.lines - a.lines)
    .slice(0, 5)
  const libraries = report.dependencies.external
    .filter((dep) => dep.kind === 'external' && file.packages.includes(dep.package))
  const dir = file.path.includes('/') ? file.path.slice(0, file.path.lastIndexOf('/') + 1) : ''
  const usedBy = file.used_by.filter((p) => !report.files.find((f) => f.path === p)?.is_test)

  return (
    <aside className={`inspector ${floating ? 'floating' : ''}`} aria-label={`Details for ${file.path}`}>
      <div className="inspector-head">
        <div className="path">{dir || 'Repository root'}</div>
        <h2>{file.path.split('/').pop()}</h2>
        <span className="role" title={file.role_reason}>{file.role_label}</span>
        <button className="icon-btn close" onClick={onClose} aria-label="Close details">
          <CloseIcon />
        </button>
      </div>
      <div className="inspector-body">
        {file.status === 'failed' && (
          <p className="muted">Could not be analyzed: {file.failure}</p>
        )}
        <p><Rich text={file.description} /></p>

        <div className="inspector-actions">
          <button className="btn" onClick={() => onOpenCode(file.path)}>Read the code</button>
          {onToggleImpact && (
            <button className={`btn ${impactOn ? 'toggle-on' : ''}`} onClick={onToggleImpact}
              aria-pressed={impactOn} disabled={file.impact === 0}
              title="Highlight every file that depends on this one, directly or indirectly">
              {file.impact === 0 ? 'Nothing depends on it' :
                impactOn ? 'Hide what depends on it' : `Show what depends on it (${file.impact})`}
            </button>
          )}
        </div>

        <div className="metrics">
          <div><b>{file.loc.toLocaleString()}</b><span>lines of code</span></div>
          <div><b>{file.functions}</b><span>functions</span></div>
          <div><b>{file.max_complexity || '–'}</b><span>max complexity</span></div>
          <div><b>{file.fan_in}</b><span>files use it</span></div>
          <div><b>{file.fan_out}</b><span>files it uses</span></div>
          <div><b>{file.impact}</b><span>affected by a change</span></div>
        </div>

        {issues.length > 0 && (
          <div className="rel">
            <h3>{plural(issues.length, 'issue')} here</h3>
            <ul>
              {issues.map((issue) => (
                <li key={issue.id} style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
                  <Sev level={issue.severity} />
                  <button className="link-btn" onClick={() => onOpenIssue(issue.id)}>
                    <Rich text={issue.title} />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="rel">
          <h3>Used by</h3>
          <FileList paths={usedBy} onSelect={onSelect} empty="No other file imports it." />
        </div>
        <div className="rel">
          <h3>Uses</h3>
          <FileList paths={file.uses} onSelect={onSelect}
            empty="It imports no other file in this repository." />
        </div>
        {!file.is_test && (
          <div className="rel">
            <h3>Tested by</h3>
            <FileList paths={file.tested_by.filter((p) => !p.endsWith('conftest.py'))}
              onSelect={onSelect} empty="No test file imports it directly." />
          </div>
        )}

        {hot.length > 0 && (
          <div className="rel">
            <h3>Most complex functions</h3>
            <ul>
              {hot.map((fn) => (
                <li key={`${fn.name}-${fn.start}`}>
                  <button className="link-btn" onClick={() => onOpenCode(file.path, fn.start)}>
                    {fn.name}
                  </button>{' '}
                  <span className="faint" style={{ fontSize: 13 }}>
                    complexity {fn.complexity}, {plural(fn.lines, 'line')}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {libraries.length > 0 && (
          <div className="rel">
            <h3>Libraries</h3>
            <div className="chips">
              {libraries.map((dep) => <span key={dep.package} className="chip">{dep.display}</span>)}
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}
