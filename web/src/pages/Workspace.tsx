import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { type AiStatus, api, type Report, workspacePath } from '../api'
import { exampleQuestions } from '../askView'
import { Logo, ThemeToggle } from '../components/bits'
import { ChatButton, type ChatMessage, ChatPanel } from '../components/Chat'
import { CommandBox } from '../components/CommandBox'
import { Files } from '../components/Files'
import { Inspector } from '../components/Inspector'
import { Issues } from '../components/Issues'
import { MapView } from '../components/MapView'
import { Overview } from '../components/Overview'
import { useChatPosition } from '../useChatPosition'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'map', label: 'Map' },
  { id: 'issues', label: 'Issues' },
  { id: 'files', label: 'Files' },
]

export function Workspace() {
  const { snapshotId: rawId = '', tab = 'overview' } = useParams()
  const snapshotId = decodeURIComponent(rawId)
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [impactOn, setImpactOn] = useState(false)
  const [cited, setCited] = useState<string[] | null>(null)
  const [chat, setChat] = useState<'closed' | 'open' | 'minimised'>('closed')
  // Messages belong to one repository; switching repositories starts a fresh log.
  const [chatLog, setChatLog] = useState<{ snapshot: string; messages: ChatMessage[] }>({ snapshot: '', messages: [] })
  const [ai, setAi] = useState<AiStatus | null>(null)
  const historyFor = useRef<string | null>(null)
  const chatPosition = useChatPosition()
  const messages = chatLog.snapshot === snapshotId ? chatLog.messages : []

  const selected = params.get('file')
  const line = params.get('line') ? Number(params.get('line')) : null
  const issueId = params.get('issue')

  useEffect(() => {
    let cancelled = false
    setReport(null)
    setError(null)
    api.report(snapshotId)
      .then((r) => { if (!cancelled) setReport(r) })
      .catch((e: Error) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [snapshotId])

  useEffect(() => {
    if (report) document.title = `${report.snapshot.repo.name} · EvidenceLoop`
  }, [report])

  useEffect(() => setImpactOn(false), [selected])

  const go = useCallback((nextTab: string, query: Record<string, string | number | null> = {}) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== null && value !== undefined && value !== '') search.set(key, String(value))
    }
    const qs = search.toString()
    navigate(`${workspacePath(snapshotId, nextTab)}${qs ? `?${qs}` : ''}`)
  }, [navigate, snapshotId])

  const select = useCallback((path: string | null) => {
    const next = new URLSearchParams(params)
    if (path) next.set('file', path)
    else next.delete('file')
    next.delete('line')
    setParams(next)
  }, [params, setParams])

  const openCode = useCallback((path: string, at?: number) => go('files', { file: path, line: at ?? null }), [go])
  const openIssue = useCallback((id: string | null) => go('issues', { issue: id }), [go])

  const updateMessages = useCallback((change: (list: ChatMessage[]) => ChatMessage[]) => {
    setChatLog((prev) => ({ snapshot: snapshotId, messages: change(prev.snapshot === snapshotId ? prev.messages : []) }))
  }, [snapshotId])

  const openChat = useCallback(() => {
    setChat('open')
    if (historyFor.current === snapshotId) return
    historyFor.current = snapshotId
    api.ai().then(setAi).catch(() => setAi({ available: false, reason: "Couldn't check the AI settings." }))
    api.asks(snapshotId).then((history) => {
      const earlier = [...history].reverse().map((result) => ({ id: result.id, question: result.question, result }))
      updateMessages((list) => [...earlier.filter((m) => !list.some((x) => x.result?.id === m.id)), ...list])
    }).catch(() => { /* no history: the chat starts empty */ })
  }, [snapshotId, updateMessages])

  const sendQuestion = useCallback((question: string) => {
    openChat()
    const id = `local-${Date.now()}-${Math.random().toString(36).slice(2)}`
    updateMessages((list) => [...list, { id, question }])
    api.ask(snapshotId, question)
      .then((result) => updateMessages((list) => list.map((m) => (m.id === id ? { ...m, result } : m))))
      .catch((e: Error) => updateMessages((list) => list.map((m) => (m.id === id ? { ...m, error: e.message } : m))))
  }, [snapshotId, openChat, updateMessages])

  const showInMap = useCallback((paths: string[]) => {
    setCited(paths)
    setChat('minimised')  // out of the way, so the lit files are visible
    go('map')
  }, [go])

  if (error) {
    return (
      <div className="centered">
        <div>
          <h1 className="lede" style={{ margin: '0 auto 12px' }}>This repository couldn't be opened.</h1>
          <p>{error}</p>
          <p><Link to="/">Back to your repositories</Link></p>
        </div>
      </div>
    )
  }

  if (!report) {
    return <div className="centered">Reading the analysis…</div>
  }

  const repo = report.snapshot.repo
  const selectedFile = selected ? report.files.find((f) => f.path === selected) ?? null : null
  const issueCount = report.insights.length
  const hot = (report.summary.totals.by_severity.high ?? 0) > 0

  const busy = messages.some((m) => !m.result && !m.error)

  const inspector = selectedFile && tab !== 'files' && (
    <Inspector
      report={report}
      file={selectedFile}
      floating={tab !== 'map'}
      onSelect={(p) => select(p)}
      onOpenCode={openCode}
      onOpenIssue={(id) => openIssue(id)}
      onClose={() => select(null)}
      impactOn={impactOn}
      onToggleImpact={tab === 'map' ? () => setImpactOn((v) => !v) : undefined}
    />
  )

  return (
    <div className="workspace">
      <header className="topbar">
        <Link to="/" className="wordmark" aria-label="EvidenceLoop home"><Logo /></Link>
        <div className="divider-v" aria-hidden="true" />
        <div className="repo-id">
          <strong>{repo.owner ? `${repo.owner} / ` : ''}{repo.name}</strong>
          {report.snapshot.commit && (
            repo.url ? (
              <a className="commit" href={`${repo.url}/tree/${report.snapshot.commit}`} target="_blank"
                rel="noreferrer" title="The exact commit this analysis describes">
                at {report.snapshot.commit.slice(0, 7)}
              </a>
            ) : <span className="commit">at {report.snapshot.commit.slice(0, 7)}</span>
          )}
        </div>
        <div className="spacer" />
        <CommandBox snapshotId={snapshotId} onPick={(path, at) => openCode(path, at)} onAsk={sendQuestion} />
        <ThemeToggle />
      </header>

      <nav className="tabs" aria-label="Repository views">
        {TABS.map((t) => (
          <Link key={t.id} to={workspacePath(snapshotId, t.id) + (selected && t.id !== 'issues' ? `?file=${encodeURIComponent(selected)}` : '')}
            className={`tab ${tab === t.id ? 'active' : ''}`} aria-current={tab === t.id ? 'page' : undefined}>
            {t.label}
            {t.id === 'issues' && issueCount > 0 && <span className={`badge ${hot ? 'hot' : ''}`}>{issueCount}</span>}
          </Link>
        ))}
      </nav>

      <main className="stage">
        {tab === 'overview' && (
          <div className="scroll">
            <Overview report={report} onSelect={(p) => select(p)} onOpenIssue={(id) => openIssue(id)}
              onTab={(t) => go(t)} />
          </div>
        )}
        {tab === 'map' && (
          <MapView report={report} selected={selected} onSelect={select} impactOn={impactOn}
            highlight={cited} onClearHighlight={() => setCited(null)} />
        )}
        {tab === 'issues' && (
          <div className="scroll">
            <Issues report={report} openId={issueId} onOpenId={(id) => openIssue(id)} onOpenCode={openCode} />
          </div>
        )}
        {tab === 'files' && (
          <Files report={report} path={selected} line={line}
            onOpen={(path, at) => go('files', { file: path, line: at ?? null })}
            onOpenIssue={(id) => openIssue(id)} />
        )}
        {inspector}
      </main>

      {chat === 'open' ? (
        <ChatPanel anchor={chatPosition.pos} repoName={repo.name} ai={ai} messages={messages} examples={exampleQuestions(report)}
          onSend={sendQuestion} onMinimise={() => setChat('minimised')} onClose={() => setChat('closed')}
          onOpenCode={(path, at) => { setChat('minimised'); openCode(path, at) }} onShowInMap={showInMap} />
      ) : (
        <ChatButton busy={busy} shifted={!!inspector} pos={chatPosition.pos} onMove={chatPosition.move}
          onClick={openChat} />
      )}
    </div>
  )
}
