import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Explanation, type FileInfo } from '../api'
import { explainStep, firstSentence } from '../explainState'
import { Rich } from './bits'

/** Explanations already fetched this visit, so moving between views doesn't ask again. */
const known = new Map<string, Explanation>()

type View =
  | { key: string; phase: 'facts' }
  | { key: string; phase: 'writing' }
  | { key: string; phase: 'error'; error: string }

interface Props {
  snapshotId: string
  file: FileInfo
  onOpenLine: (line: number) => void
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
        if (res.status === 'ready' && res.explanation) {
          known.set(asked, res.explanation)
          setVersion((v) => v + 1)
        }
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
        const step = explainStep(res, mayWrite)
        if (step === 'show') {
          known.set(key, res.explanation!)
          setVersion((v) => v + 1)
        } else if (step === 'write') {
          write(false)
        } else {
          setView({ key, phase: 'facts' })
        }
      })
      .catch(() => { if (!cancelled) setView({ key, phase: 'facts' }) })
    return () => { cancelled = true }
  }, [key, snapshotId, path, mayWrite, write])

  const current = view?.key === key ? view : null
  const writing = current?.phase === 'writing'
  return {
    explanation: writing ? undefined : known.get(key),
    writing,
    error: current?.phase === 'error' ? current.error : null,
    regenerate: () => { known.delete(key); write(true) },
    retry: () => write(false),
  }
}

/** "What is this file?": an AI-written paragraph and where to start reading, over the
 * deterministic description. Without an API key only the description shows. */
export function FileExplanation({ snapshotId, file, onOpenLine, collapsible = false }: Props) {
  const { explanation, writing, error, regenerate, retry } = useExplanation(snapshotId, file.path, true)
  const [folded, setFolded] = useState(() => collapsible && readFolded())
  const fold = (next: boolean) => {
    setFolded(next)
    try {
      localStorage.setItem(FOLD_KEY, next ? '1' : '0')
    } catch {
      /* private mode: the choice lasts for this visit */
    }
  }

  if (explanation && folded) {
    return (
      <div className="explain">
        <button className="link-btn explain-unfold" onClick={() => fold(false)} aria-expanded="false">
          ▸ What this file does
        </button>
        <p className="explain-facts"><Rich text={file.description} /></p>
      </div>
    )
  }

  return (
    <div className="explain">
      {explanation && (
        <section className="explain-ai" aria-label="What this file does">
          <h3>
            {collapsible ? (
              <button className="link-btn explain-fold" onClick={() => fold(true)} aria-expanded="true"
                title="Fold this away to give the code more room">▾ What this file does</button>
            ) : 'What this file does'}
          </h3>
          <p>{explanation.summary}</p>
          {explanation.key_parts.length > 0 && (
            <>
              <h4>Start reading here</h4>
              <ul className="key-parts">
                {explanation.key_parts.map((part) => (
                  <li key={`${part.symbol}-${part.start}`}>
                    <button className="link-btn" onClick={() => onOpenLine(part.start)}
                      title={`Open ${part.symbol} at line ${part.start}`}>
                      <code>{part.symbol.split('.').pop()}{part.kind === 'class' ? '' : '()'}</code>
                    </button>
                    <span className="lines">lines {part.start}–{part.end}</span>
                    <span className="does">{part.does}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          <div className="explain-note">
            <span>Written by AI from the code and the facts below.</span>
            <button className="link-btn" onClick={regenerate}>Regenerate</button>
            <details>
              <summary>What was sent</summary>
              <p>{explanation.sent} Model: {explanation.model}.</p>
            </details>
          </div>
        </section>
      )}
      {writing && <p className="explain-wait" role="status">Writing a plain-English explanation of this file…</p>}
      {error && (
        <p className="explain-error" role="status">
          Couldn't write an explanation: {error}{' '}
          <button className="link-btn" onClick={retry}>Try again</button>
        </p>
      )}
      <p className={explanation ? 'explain-facts' : undefined}><Rich text={file.description} /></p>
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
