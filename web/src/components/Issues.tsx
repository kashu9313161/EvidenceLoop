import { useEffect, useMemo, useRef, useState } from 'react'
import type { Issue, Report, Severity } from '../api'
import { fileName, plural } from '../util'
import { Rich, Sev } from './bits'
import { CodeBlock } from './CodeBlock'

interface Props {
  report: Report
  openId: string | null
  onOpenId: (id: string | null) => void
  onOpenCode: (path: string, line?: number) => void
}

const SEVERITIES: Severity[] = ['high', 'medium', 'low']

function IssueBody({ issue, onOpenCode }: { issue: Issue; onOpenCode: Props['onOpenCode'] }) {
  return (
    <div className="issue-body">
      <div className="advice">
        <div>
          <h4>Why it matters</h4>
          <p><Rich text={issue.why} /></p>
        </div>
        <div>
          <h4>What to do</h4>
          <p><Rich text={issue.suggestion} /></p>
        </div>
      </div>
      <p className="confidence">
        {issue.confidence === 'confirmed'
          ? 'Found exactly at these lines. Whether it matters here is still your call.'
          : 'Likely: a strong signal, but worth a quick human check.'}
      </p>
      {issue.evidence.map((evidence, index) => (
        <div className="evidence" key={`${evidence.path}-${evidence.start}-${index}`}>
          <div className="evidence-head">
            <span>
              <button className="link-btn" onClick={() => onOpenCode(evidence.path, evidence.start)}>
                {evidence.path}
              </button>
              <span className="faint">
                {' '}line{evidence.end > evidence.start ? `s ${evidence.start}–${evidence.end}` : ` ${evidence.start}`}
              </span>
            </span>
            <span className="faint">{evidence.label !== evidence.path ? evidence.label : ''}</span>
          </div>
          {evidence.snippet.lines.length > 0 && (
            <>
              <CodeBlock
                code={evidence.snippet.lines.join('\n')}
                path={evidence.path}
                startLine={evidence.snippet.start}
                highlight={evidence.end - evidence.start < 4 ? [evidence.start, evidence.end] : null}
              />
              {evidence.snippet.truncated && (
                <div className="code-truncated">
                  continues to line {evidence.end}.{' '}
                  <button className="link-btn" onClick={() => onOpenCode(evidence.path, evidence.start)}>
                    Read it all
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      ))}
    </div>
  )
}

export function Issues({ report, openId, onOpenId, onOpenCode }: Props) {
  const [severity, setSeverity] = useState<Severity | null>(null)
  const [category, setCategory] = useState<string | null>(null)
  const openRef = useRef<HTMLDivElement | null>(null)

  const categories = useMemo(() => {
    const counts = new Map<string, number>()
    for (const issue of report.insights) counts.set(issue.category, (counts.get(issue.category) ?? 0) + 1)
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [report])

  const shown = report.insights.filter(
    (issue) => (!severity || issue.severity === severity) && (!category || issue.category === category),
  )

  useEffect(() => {
    openRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [openId])

  if (report.insights.length === 0) {
    return (
      <div className="page">
        <h1 className="lede">No issues found.</h1>
        <p className="facts">
          None of the current checks matched: no import cycles, no overly complex functions, no
          silenced errors, and dependencies are declared. That isn't proof of correctness, so read
          the most depended-on files on the Overview next.
        </p>
      </div>
    )
  }

  return (
    <div className="page">
      <h1 className="lede">
        {plural(report.insights.length, 'issue')}, ranked by how far each one spreads.
      </h1>
      <p className="facts" style={{ marginBottom: 32 }}>
        Each one points at the exact lines it comes from. Issues in files that many others depend on
        rank higher, because a problem there reaches more of the code.
      </p>

      <div className="filters">
        <div className="group" role="group" aria-label="Filter by severity">
          <span className="label">Severity</span>
          {SEVERITIES.filter((s) => report.summary.totals.by_severity[s]).map((s) => (
            <button key={s} className={`chip ${severity === s ? 'on' : ''}`} aria-pressed={severity === s}
              onClick={() => setSeverity(severity === s ? null : s)}>
              {s[0].toUpperCase() + s.slice(1)} {report.summary.totals.by_severity[s]}
            </button>
          ))}
        </div>
        <div className="group" role="group" aria-label="Filter by kind">
          <span className="label">Kind</span>
          {categories.map(([name, count]) => (
            <button key={name} className={`chip ${category === name ? 'on' : ''}`}
              aria-pressed={category === name} onClick={() => setCategory(category === name ? null : name)}>
              {name} {count}
            </button>
          ))}
        </div>
      </div>

      {shown.map((issue) => {
        const open = issue.id === openId
        const where = issue.files.length === 1 ? fileName(issue.files[0])
          : issue.files.length > 1 ? plural(issue.files.length, 'file') : 'Whole project'
        return (
          <div className="issue" key={issue.id} ref={open ? openRef : undefined}>
            <button onClick={() => onOpenId(open ? null : issue.id)} aria-expanded={open}>
              <Sev level={issue.severity} />
              <span className="title"><Rich text={issue.title} /></span>
              <span className="meta">{issue.category} in {where}</span>
              <span className="summary"><Rich text={issue.summary} /></span>
            </button>
            {open && <IssueBody issue={issue} onOpenCode={onOpenCode} />}
          </div>
        )
      })}
      {shown.length === 0 && <p className="muted">No issues match these filters.</p>}
    </div>
  )
}
