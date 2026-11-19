import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Explanation, type FileFacts, type FileInfo } from '../api'
import { fileRows } from '../cardRows'
import { explainStep, firstSentence } from '../explainState'
import { Rich } from './bits'
import { SourceTag } from './FunctionCard'

/** Explanations and code facts already fetched this visit, so moving between views doesn't
 * ask again. */
const known = new Map<string, Explanation>()
const knownFacts = new Map<string, FileFacts | null>()

type View =
  | { key: string; phase: 'facts'; aiOff?: boolean }
  | { key: string; phase: 'writing' }
  | { key: string; phase: 'error'; error: string }

interface Props {
  snapshotId: string
  file: FileInfo
  onOpenLine: (line: number) => void
  /** Light up a step's lines in the code while the pointer is on it (null to clear). */
  onHighlight?: (range: [number, number] | null) => void
  /** Above the code, where space matters: the reader can fold it away (remembered). */
  collapsible?: boolean
}

const FOLD_KEY = 'el-explain-folded'

function readFolded(): boolean {
  try {
    return localStorage.getItem(FOLD_KEY) === '1'
  } catch {
    return false
  }
}

/** Ask the server once per file: show a cached explanation, write a missing one if `mayWrite`,
 * or fall back to the facts. Responses for a file that is no longer shown are ignored. */
function useExplanation(snapshotId: string, path: string, mayWrite: boolean) {
  const key = `${snapshotId}|${path}`
  const [view, setView] = useState<View | null>(null)
  const [, setVersion] = useState(0)
  const shown = useRef(key)
  useEffect(() => { shown.current = key }, [key])

  const write = useCallback((refresh: boolean) => {
    const asked = key
    setView({ key: asked, phase: 'writing' })
    api.explain(snapshotId, path, refresh)
      .then((res) => {
        if (res.facts !== undefined) knownFacts.set(asked, res.facts)
        if (res.status === 'ready' && res.explanation) known.set(asked, res.explanation)
        setVersion((v) => v + 1)
        if (shown.current === asked) setView({ key: asked, phase: 'facts' })
      })
      .catch((e: Error) => {
        if (shown.current === asked) setView({ key: asked, phase: 'error', error: e.message })
      })
  }, [key, snapshotId, path])

  useEffect(() => {
    if (known.has(key)) return
    let cancelled = false
    api.explanation(snapshotId, path)
      .then((res) => {
        if (cancelled) return
        knownFacts.set(key, res.facts ?? null)
        setVersion((v) => v + 1)
        const step = explainStep(res, mayWrite)
        if (step === 'show') {
          known.set(key, res.explanation!)
        } else if (step === 'write') {
          write(false)
        } else {
          setView({ key, phase: 'facts', aiOff: !res.available })
        }
      })
      .catch(() => { if (!cancelled) setView({ key, phase: 'facts' }) })
    return () => { cancelled = true }
  }, [key, snapshotId, path, mayWrite, write])

  const current = view?.key === key ? view : null
  const writing = current?.phase === 'writing'
  return {
    explanation: writing ? undefined : known.get(key),
    facts: knownFacts.get(key),
    writing,
    error: current?.phase === 'error' ? current.error : null,
    aiOff: current?.phase === 'facts' && !!current.aiOff,
    regenerate: () => { known.delete(key); write(true) },
    retry: () => write(false),
  }
}

/** "What is this file?": five questions (what it is for, what goes in, what happens, what
 * comes out, why it is needed). The code answers each one; with an API key the AI's checked
 * answers replace them. Each row says where it came from. */
export function FileExplanation({ snapshotId, file, onOpenLine, onHighlight, collapsible = false }: Props) {
  const { explanation, facts, writing, error, aiOff, regenerate, retry } = useExplanation(snapshotId, file.path, true)
  const [folded, setFolded] = useState(() => collapsible && readFolded())
  const fold = (next: boolean) => {
    setFolded(next)
    try {
      localStorage.setItem(FOLD_KEY, next ? '1' : '0')
    } catch {
      /* private mode: the choice lasts for this visit */
    }
  }
  const rows = fileRows(explanation, facts)
  const purpose = rows.find((r) => r.key === 'purpose')

  if (collapsible && folded) {
    return (
      <div className="explain">
        <button className="link-btn explain-unfold" onClick={() => fold(false)} aria-expanded="false">
          ▸ What this file does
        </button>
        <p className="explain-facts">{purpose ? purpose.text : <Rich text={file.description} />}</p>
      </div>
    )
  }

  return (
    <div className="explain">
      {rows.length > 0 ? (
        <section className="explain-ai" aria-label="What this file does">
          <h3>
            {collapsible ? (
              <button className="link-btn explain-fold" onClick={() => fold(true)} aria-expanded="true"
                title="Fold this away to give the code more room">▾ What this file does</button>
            ) : 'What this file does'}
          </h3>
          <dl className="card-rows">
            {rows.map((row) => (
              <div className="card-row" key={row.key}>
                <dt>{row.label} <SourceTag source={row.source} /></dt>
                <dd>
                  {row.steps ? (
                    <ul className="key-parts">
                      {row.steps.map((part) => (
                        <li key={`${part.symbol}-${part.start}`}
                          onMouseEnter={() => onHighlight?.([part.start, part.end])}
                          onMouseLeave={() => onHighlight?.(null)}>
                          <button className="link-btn"
                            onClick={() => { onHighlight?.([part.start, part.end]); onOpenLine(part.start) }}
                            onFocus={() => onHighlight?.([part.start, part.end])}
                            onBlur={() => onHighlight?.(null)}
                            title={`Open ${part.symbol} at line ${part.start}`}>
                            <code>{part.symbol.split('.').pop()}{part.kind === 'class' ? '' : '()'}</code>
                          </button>
                          <span className="lines">lines {part.start}–{part.end}</span>
                          <span className="does">{part.does}</span>
                        </li>
                      ))}
                    </ul>
                  ) : row.text}
                </dd>
              </div>
            ))}
          </dl>
          {explanation && (
            <div className="explain-note">
              <span>AI rows were written from the code and the facts.</span>
              <button className="link-btn" onClick={regenerate}>Regenerate</button>
              <details>
                <summary>What was sent</summary>
                <p>{explanation.sent} Model: {explanation.model}.</p>
              </details>
            </div>
          )}
        </section>
      ) : (
        <p><Rich text={file.description} /></p>
      )}
      {writing && <p className="explain-wait" role="status">Writing a plain-English explanation of this file…</p>}
      {error && (
        <p className="explain-error" role="status">
          Couldn't write an explanation: {error}{' '}
          <button className="link-btn" onClick={retry}>Try again</button>
        </p>
      )}
      {aiOff && collapsible && (
        <p className="explain-hint">
          These answers come from the code. Want them in plain English? Set <code>GEMINI_API_KEY</code> in
          the terminal, then restart EvidenceLoop.
        </p>
      )}
    </div>
  )
}

/** One line for a list: the first sentence of a cached explanation, otherwise `fallback`.
 * Never writes one, so a list of files costs nothing. */
export function ExplanationGlance({ snapshotId, file, fallback }: {
  snapshotId: string
  file: FileInfo
  fallback: string
}) {
  const { explanation } = useExplanation(snapshotId, file.path, false)
  return <span className="why">{explanation ? firstSentence(explanation.summary) : fallback}</span>
}
