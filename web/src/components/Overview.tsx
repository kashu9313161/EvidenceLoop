import type { Flow, Report } from '../api'
import { areaLabel, areaPalette, fileName, plural, useTheme } from '../util'
import { Rich, Sev } from './bits'

interface Props {
  report: Report
  onSelect: (path: string) => void
  onOpenIssue: (id: string) => void
  onTab: (tab: string) => void
}

function FlowTree({ flow, onSelect }: { flow: Flow; onSelect: (path: string) => void }) {
  const children = new Map<string, typeof flow.steps>()
  for (const step of flow.steps.slice(1)) {
    const list = children.get(step.parent ?? '') ?? []
    list.push(step)
    children.set(step.parent ?? '', list)
  }
  const rows: { path: string; depth: number; line: number | null; last: boolean }[] = []
  const walk = (path: string, depth: number) => {
    const kids = children.get(path) ?? []
    kids.forEach((kid, index) => {
      rows.push({ path: kid.path, depth, line: kid.line, last: index === kids.length - 1 })
      walk(kid.path, depth + 1)
    })
  }
  walk(flow.entry, 0)
  const shown = rows.slice(0, 10)

  return (
    <div className="flow">
      <div className="flow-head">
        <button className="link-btn" onClick={() => onSelect(flow.entry)} title={flow.entry}>
          <strong>{fileName(flow.entry)}</strong>
        </button>
        <span className="kind">{flow.reason || flow.kind}</span>
      </div>
      {shown.length > 0 ? (
        <ul>
          {shown.map((row, index) => (
            <li key={`${row.path}-${index}`}>
              <span className="branch" aria-hidden="true">
                {'   '.repeat(row.depth)}{row.last ? '└─ ' : '├─ '}
              </span>
              <button className="link-btn" onClick={() => onSelect(row.path)} title={row.path}>
                {fileName(row.path)}
              </button>
              {row.line && <span className="line-ref">line {row.line}</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="more">Imports no other file in the repository.</p>
      )}
      {rows.length > shown.length && (
        <p className="more">and {plural(rows.length - shown.length, 'more file')}</p>
      )}
    </div>
  )
}

export function Overview({ report, onSelect, onOpenIssue, onTab }: Props) {
  const [theme] = useTheme()
  const { summary, coverage, architecture } = report
  const totals = summary.totals
  const colours = areaPalette(architecture.areas, theme)
  const totalLoc = architecture.areas.reduce((sum, area) => sum + area.loc, 0) || 1
  const topIssues = report.insights.slice(0, 5)
  const stackByCategory = new Map<string, typeof summary.stack>()
  for (const item of summary.stack) {
    const list = stackByCategory.get(item.category) ?? []
    list.push(item)
    stackByCategory.set(item.category, list)
  }
  const analyzedPct = coverage.discovered
    ? Math.round(((coverage.analyzed + coverage.indexed) / coverage.discovered) * 100)
    : 0
  const high = totals.by_severity.high ?? 0

  return (
    <div className="page">
      <h1 className="lede">{summary.headline}</h1>
      {summary.readme && (
        <blockquote className="readme-quote">
          {summary.readme}
          <cite>From {summary.readme_path}</cite>
        </blockquote>
      )}
      <p className="facts">{summary.facts.join(' ')}</p>

      <div className="statline" role="list" aria-label="Key numbers">
        <div className="stat" role="listitem"><b>{totals.source_files}</b><span>source files</span></div>
        <div className="stat" role="listitem"><b>{totals.source_loc.toLocaleString()}</b><span>lines of source code</span></div>
        <div className="stat" role="listitem"><b>{totals.connections}</b><span>connections between files</span></div>
        <div className="stat" role="listitem"><b>{totals.test_files}</b><span>test files</span></div>
        <div className="stat" role="listitem"><b>{totals.packages}</b><span>third-party packages</span></div>
        <div className={`stat ${high ? 'alert' : ''}`} role="listitem">
          <b>{totals.insights}</b>
          <span>{high ? `issues, ${high} high severity` : 'issues found'}</span>
        </div>
      </div>

      <div className="columns">
        <div>
          <section className="section">
            <div className="section-head">
              <h2>How it's organised</h2>
              <button className="link-btn hint" onClick={() => onTab('map')}>Open the map</button>
            </div>
            <div className="area-strip" aria-hidden="true">
              {architecture.areas.map((area) => (
                <span key={area.name} title={`${areaLabel(area.name)}: ${area.loc} lines`}
                  style={{ flex: Math.max(area.loc / totalLoc, 0.02), background: colours[area.name] }} />
              ))}
            </div>
            {architecture.areas.map((area) => (
              <div className="area-row" key={area.name}>
                <span className="swatch" style={{ background: colours[area.name] }} />
                <div>
                  <h3>{areaLabel(area.name)}</h3>
                  <p>
                    {area.description.split('Centre of gravity: ')[0]}
                    Most connected:{' '}
                    <button className="link-btn" onClick={() => onSelect(area.key_file)}>
                      {fileName(area.key_file)}
                    </button>.
                  </p>
                  {area.depends_on.length > 0 && (
                    <div className="deps">
                      Depends on {area.depends_on.map((d, i) => (
                        <span key={d.area}>{i > 0 && ', '}{areaLabel(d.area)} ({plural(d.edges, 'import')})</span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </section>

          {architecture.flows.length > 0 && (
            <section className="section">
              <div className="section-head">
                <h2>Where it starts</h2>
                <span className="hint">Entry points and the files they pull in</span>
              </div>
              {architecture.flows.map((flow) => (
                <FlowTree key={flow.entry} flow={flow} onSelect={onSelect} />
              ))}
            </section>
          )}

          {architecture.routes.length > 0 && (
            <section className="section">
              <div className="section-head"><h2>HTTP routes</h2></div>
              <table className="routes">
                <tbody>
                  {architecture.routes.map((route) => (
                    <tr key={`${route.method}${route.path}${route.file}`}>
                      <td><span className="method">{route.method}</span></td>
                      <td><code>{route.path}</code></td>
                      <td>
                        <button className="link-btn" onClick={() => onSelect(route.file)}>
                          {route.handler}()
                        </button>{' '}
                        <span className="faint">in {fileName(route.file)}, line {route.line}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </div>

        <div>
          <section className="section">
            <div className="section-head">
              <h2>Fix these first</h2>
              {report.insights.length > 0 && (
                <button className="link-btn hint" onClick={() => onTab('issues')}>
                  All {plural(report.insights.length, 'issue')}
                </button>
              )}
            </div>
            {topIssues.length === 0 ? (
              <p className="muted">No issues found by the current checks.</p>
            ) : (
              topIssues.map((issue) => (
                <div className="issue-row" key={issue.id}>
                  <Sev level={issue.severity} />
                  <button className="link-btn title" onClick={() => onOpenIssue(issue.id)}
                    style={{ color: 'var(--paper)' }}>
                    <Rich text={issue.title} />
                  </button>
                  <span className="summary"><Rich text={issue.summary} /></span>
                </div>
              ))
            )}
          </section>

          <section className="section">
            <div className="section-head"><h2>Files that matter most</h2></div>
            <p className="lead-in">Ranked by how much of the code depends on them.</p>
            {summary.key_files.filter((k) => k.impact > 0).slice(0, 5).map((key) => (
              <div className="key-file" key={key.path}>
                <button className="link-btn" onClick={() => onSelect(key.path)} title={key.path}>
                  {fileName(key.path)}
                </button>
                <span className="num">{plural(key.impact, 'dependent')}</span>
                <span className="why">{key.why}</span>
              </div>
            ))}
            {summary.key_files.every((k) => k.impact === 0) && (
              <p className="muted">No file is imported by another one yet.</p>
            )}
          </section>

          {summary.stack.length > 0 && (
            <section className="section">
              <div className="section-head"><h2>Built with</h2></div>
              {[...stackByCategory.entries()].map(([category, items]) => (
                <div className="stack-group" key={category}>
                  <span className="cat">{category}</span>
                  <div className="chips">
                    {items.map((item) => (
                      <span className="chip" key={item.name}
                        title={item.files ? `Imported in ${plural(item.files, 'file')}` : `Found in ${item.evidence}`}>
                        {item.name}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </section>
          )}

          <section className="section">
            <div className="section-head"><h2>What was analysed</h2></div>
            <div className="coverage-bar" aria-hidden="true">
              <span style={{ flex: coverage.analyzed, background: 'var(--line)' }} />
              <span style={{ flex: coverage.indexed, background: 'var(--line-soft)' }} />
              <span style={{ flex: coverage.failed, background: 'var(--signal)' }} />
              <span style={{ flex: coverage.skipped, background: 'transparent' }} />
            </div>
            <ul className="coverage-list">
              <li><span>Code files parsed</span><b>{coverage.analyzed}</b></li>
              <li><span>Other text files indexed for search</span><b>{coverage.indexed}</b></li>
              <li><span>Could not be read</span><b>{coverage.failed}</b></li>
              <li><span>Skipped (binary, unsupported or tooling folders)</span><b>{coverage.skipped}</b></li>
              <li>
                <span>Project imports resolved to a file</span>
                <b>{Math.round(coverage.imports.resolution_rate * 100)}%</b>
              </li>
            </ul>
            <p className="faint" style={{ fontSize: 13.5, marginTop: 10 }}>
              {analyzedPct}% of the {plural(coverage.discovered, 'file')} in the repository were read.
              Connections are {coverage.confirmed_edges} confirmed
              {coverage.inferred_edges > 0 && ` and ${coverage.inferred_edges} inferred (best guess)`}.
              Nothing from the repository was run.
            </p>
            {(coverage.skipped_files.length > 0 || coverage.failed_files.length > 0) && (
              <details className="disclosure">
                <summary>See which files were left out, and why</summary>
                <ul>
                  {[...coverage.failed_files, ...coverage.skipped_files].slice(0, 80).map((f) => (
                    <li key={f.path}><code>{f.path}</code> <span className="faint">{f.reason}</span></li>
                  ))}
                </ul>
              </details>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
