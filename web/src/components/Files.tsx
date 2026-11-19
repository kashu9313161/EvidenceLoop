import { useEffect, useMemo, useState } from 'react'
import { api, type FileDetail, type FileInfo, type Report } from '../api'
import { plainFileNote } from '../fileNote'
import { fileName, plural } from '../util'
import { Rich, Sev } from './bits'
import { CodeBlock } from './CodeBlock'
import { FileExplanation } from './FileExplanation'

interface Props {
  report: Report
  path: string | null
  line: number | null
  onOpen: (path: string, line?: number) => void
  onOpenIssue: (id: string) => void
}

/** A row in the tree: analysed code, or a text file (README, docs, config) shown as text. */
type TreeFile = Pick<FileInfo, 'path' | 'name' | 'status' | 'role_label' | 'role_reason' | 'severity'> & {
  text?: boolean
}

interface Folder {
  name: string
  path: string
  folders: Map<string, Folder>
  files: TreeFile[]
  hasCode: boolean
}

function buildTree(files: TreeFile[]): Folder {
  const root: Folder = { name: '', path: '', folders: new Map(), files: [], hasCode: false }
  for (const file of files) {
    const parts = file.path.split('/')
    let node = root
    if (!file.text) root.hasCode = true
    parts.slice(0, -1).forEach((part, index) => {
      if (!node.folders.has(part)) {
        node.folders.set(part, {
          name: part, path: parts.slice(0, index + 1).join('/'), folders: new Map(), files: [], hasCode: false,
        })
      }
      node = node.folders.get(part)!
      if (!file.text) node.hasCode = true
    })
    node.files.push(file)
  }
  return root
}

const textFile = (path: string): TreeFile => ({
  path, name: path.split('/').pop() ?? path, status: 'indexed', role_label: 'Text',
  role_reason: 'not analysed code; shown as text and included in search', severity: null, text: true,
})

const SEVERITY_COLOUR = { high: 'var(--signal)', medium: 'var(--amber)', low: 'var(--low)' }

/** Folders with code start open and text-only folders (docs) start closed; a click flips that.
 * While filtering, every folder is open. */
function TreeFolder({ folder, selected, onOpen, toggled, toggle, filtering }: {
  folder: Folder
  selected: string | null
  onOpen: (path: string) => void
  toggled: Set<string>
  toggle: (path: string) => void
  filtering: boolean
}) {
  const folders = [...folder.folders.values()].sort((a, b) => a.name.localeCompare(b.name))
  const files = [...folder.files].sort((a, b) => a.name.localeCompare(b.name))
  return (
    <ul>
      {folders.map((child) => {
        // Collapse single-child chains: src/pkg/ shows as one row.
        let shown = child
        let label = child.name
        while (shown.files.length === 0 && shown.folders.size === 1) {
          shown = [...shown.folders.values()][0]
          label += `/${shown.name}`
        }
        const isClosed = !filtering && (shown.hasCode ? toggled.has(shown.path) : !toggled.has(shown.path))
        return (
          <li key={shown.path}>
            <button className="folder" onClick={() => toggle(shown.path)} aria-expanded={!isClosed}>
              <span aria-hidden="true">{isClosed ? '▸' : '▾'}</span> {label}
            </button>
            {!isClosed && (
              <TreeFolder folder={shown} selected={selected} onOpen={onOpen} toggled={toggled} toggle={toggle}
                filtering={filtering} />
            )}
          </li>
        )
      })}
      {files.map((file) => (
        <li key={file.path}>
          <button
            className={`file ${selected === file.path ? 'selected' : ''} ${file.status === 'failed' ? 'muted' : ''} ${file.text ? 'text' : ''}`}
            onClick={() => onOpen(file.path)}
            aria-current={selected === file.path ? 'true' : undefined}
            title={`${file.role_label}: ${file.role_reason}`}
          >
            <span className="n">{file.path.split('/').pop()}</span>
            {file.severity && (
              <span className="mark" style={{ background: SEVERITY_COLOUR[file.severity] }}
                aria-label={`${file.severity} severity issue`} />
            )}
          </button>
        </li>
      ))}
    </ul>
  )
}

export function Files({ report, path, line, onOpen, onOpenIssue }: Props) {
  const [filter, setFilter] = useState('')
  const [toggled, setToggled] = useState<Set<string>>(new Set())
  const [detail, setDetail] = useState<FileDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [range, setRange] = useState<[number, number] | null>(null)

  const files = useMemo(() => {
    const all: TreeFile[] = [...report.files, ...(report.coverage.indexed_files ?? []).map(textFile)]
    return all.filter((f) => !filter || f.path.toLowerCase().includes(filter.toLowerCase()))
  }, [report, filter])
  const tree = useMemo(() => buildTree(files), [files])
  const selectedPath = path ?? report.summary.key_files[0]?.path ?? report.files[0]?.path ?? null
  const info = report.files.find((f) => f.path === selectedPath) ?? null

  useEffect(() => {
    if (!selectedPath) return
    let cancelled = false
    setDetail(null)
    setError(null)
    setRange(null)
    api.file(report.snapshot.id, selectedPath)
      .then((d) => { if (!cancelled) setDetail(d) })
      .catch((e: Error) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [report.snapshot.id, selectedPath])

  useEffect(() => {
    if (line && info) {
      const fn = info.function_metrics.find((f) => f.start === line)
      setRange(fn ? [fn.start, fn.end] : null)
    }
  }, [line, info])

  const toggle = (p: string) => setToggled((prev) => {
    const next = new Set(prev)
    if (next.has(p)) next.delete(p)
    else next.add(p)
    return next
  })

  const issues = info ? report.insights.filter((i) => info.insights.includes(i.id)) : []
  const complexity = new Map(info?.function_metrics.map((f) => [`${f.name}@${f.start}`, f.complexity]))

  return (
    <div className="files-layout">
      <nav className="tree" aria-label="Files">
        <input className="tree-filter" type="search" placeholder="Filter files" value={filter}
          onChange={(e) => setFilter(e.target.value)} aria-label="Filter files" />
        <TreeFolder folder={tree} selected={selectedPath} onOpen={(p) => onOpen(p)} toggled={toggled}
          toggle={toggle} filtering={!!filter} />
        {files.length === 0 && <p className="faint" style={{ padding: 8 }}>No file matches.</p>}
      </nav>

      <div className="file-view">
        {info ? (
          <>
            <header className="file-view-head">
              <div className="path">{info.path}</div>
              <h1>{info.path.split('/').pop()}</h1>
              <span className="role" title={info.role_reason}>{info.role_label}</span>{' '}
              <span className="faint" style={{ fontSize: 13.5, marginLeft: 6 }}>
                {plural(info.loc, 'line')} of code, used by {plural(info.fan_in, 'file')}, uses {plural(info.fan_out, 'file')}
              </span>
              <FileExplanation snapshotId={report.snapshot.id} file={info} collapsible
                onOpenLine={(at) => onOpen(info.path, at)} />
            </header>
            <div className="file-panels">
              <div className="file-code" data-scroller>
                {error && <p className="muted" style={{ padding: 24 }}>{error}</p>}
                {detail && detail.content == null && (
                  <p className="muted" style={{ padding: 24 }}>This file can't be shown (binary or too large).</p>
                )}
                {detail?.content != null && (
                  <CodeBlock code={detail.content.replace(/\n$/, '')} path={info.path}
                    highlight={range} focus={line ?? range?.[0] ?? null} />
                )}
                {!detail && !error && <p className="faint" style={{ padding: 24 }}>Loading code…</p>}
              </div>
              <aside className="file-side">
                {issues.length > 0 && (
                  <div className="rel">
                    <h3>Issues in this file</h3>
                    <ul>
                      {issues.map((issue) => (
                        <li key={issue.id} style={{ display: 'grid', gap: 2, marginBottom: 6 }}>
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
                  <h3>Inside this file</h3>
                  {detail && detail.symbols.length === 0 && <p className="none">No classes or functions.</p>}
                  <ul className="symbols">
                    {detail?.symbols.map((symbol) => {
                      const cx = complexity.get(`${symbol.name}@${symbol.start}`)
                      return (
                        <li key={`${symbol.name}-${symbol.start}`}>
                          <button onClick={() => { setRange([symbol.start, symbol.end]); onOpen(info.path, symbol.start) }}
                            title={`${symbol.kind}, lines ${symbol.start}–${symbol.end}`}
                            style={{ paddingLeft: 6 + (symbol.name.split('.').length - 1) * 12 }}>
                            <span className="nm">{symbol.name.split('.').pop()}{symbol.kind !== 'class' ? '()' : ''}</span>
                            <span className={`cx ${cx && cx >= 10 ? 'hot' : ''}`}>
                              {cx ? `cx ${cx}` : `L${symbol.start}`}
                            </span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                </div>
                {info.used_by.length > 0 && (
                  <div className="rel">
                    <h3>Used by</h3>
                    <ul>
                      {info.used_by.map((p) => (
                        <li key={p}><button className="link-btn" onClick={() => onOpen(p)} title={p}>{fileName(p)}</button></li>
                      ))}
                    </ul>
                  </div>
                )}
                {info.uses.length > 0 && (
                  <div className="rel">
                    <h3>Uses</h3>
                    <ul>
                      {info.uses.map((p) => (
                        <li key={p}><button className="link-btn" onClick={() => onOpen(p)} title={p}>{fileName(p)}</button></li>
                      ))}
                    </ul>
                  </div>
                )}
              </aside>
            </div>
          </>
        ) : selectedPath && (detail || error) ? (
          // Not analysed code (README, config, a skipped file): opened from search or a link.
          <>
            <header className="file-view-head">
              <div className="path">{selectedPath}</div>
              <h1>{selectedPath.split('/').pop()}</h1>
              {detail && <p className="faint">{plainFileNote(detail.status, detail.reason)}</p>}
            </header>
            <div className="file-panels">
              <div className="file-code" data-scroller>
                {error && <p className="muted" style={{ padding: 24 }}>{error}</p>}
                {detail && detail.content == null && (
                  <p className="muted" style={{ padding: 24 }}>This file can't be shown (binary or too large).</p>
                )}
                {detail?.content != null && (
                  <CodeBlock code={detail.content.replace(/\n$/, '')} path={selectedPath} focus={line} />
                )}
              </div>
            </div>
          </>
        ) : selectedPath && !error ? (
          <p className="faint" style={{ padding: 24 }}>Loading…</p>
        ) : (
          <div className="centered">Pick a file on the left.</div>
        )}
      </div>
    </div>
  )
}
