import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type FunctionCard as Card, type RowSource } from '../api'
import { cardStep, paramLabel, SIDE_EFFECT_LABELS, SOURCE_TAGS } from '../cardRows'
import { fileName, plural } from '../util'
import { CloseIcon } from './bits'

/** Cards already fetched this visit. */
const known = new Map<string, Card>()

interface Props {
  snapshotId: string
  symbolId: string
  /** Jump to a line (a caller's call, a step, a side effect). */
  onOpen: (path: string, line: number) => void
  /** Light up lines in the code while a step is hovered; null puts the function back. */
  onHighlight: (range: [number, number] | null) => void
  onClose: () => void
}

export function SourceTag({ source }: { source: RowSource }) {
  const tag = SOURCE_TAGS[source]
  return <span className={`source-tag src-${source}`} title={tag.title}>{tag.label}</span>
}

type Phase = { key: string; phase: 'loading' | 'writing' | 'ready' } | { key: string; phase: 'error'; error: string }

/** Ask once per function: show the card from the code at once, and if AI is on but this
 * function has no answer yet, write it (picking a function is the request for it). */
function useCard(snapshotId: string, symbolId: string) {
  const key = `${snapshotId}|${symbolId}`
  const [phase, setPhase] = useState<Phase>({ key, phase: 'loading' })
  const [, setVersion] = useState(0)
  const shown = useRef(key)
  useEffect(() => { shown.current = key }, [key])

  const write = useCallback((refresh: boolean) => {
    const asked = key
    setPhase({ key: asked, phase: 'writing' })
    api.explainFunction(snapshotId, symbolId, refresh)
      .then((res) => {
        if (res.card) known.set(asked, res.card)
        setVersion((v) => v + 1)
        if (shown.current !== asked) return
        setPhase(res.card ? { key: asked, phase: 'ready' } : { key: asked, phase: 'error', error: res.reason ?? 'No card.' })
      })
      .catch((e: Error) => {
        if (shown.current === asked) setPhase({ key: asked, phase: 'error', error: e.message })
      })
  }, [key, snapshotId, symbolId])

  useEffect(() => {
    const cached = known.get(key)
    if (cached && cached.ai.status !== 'missing') return  // shown from memory
    let cancelled = false
    api.functionCard(snapshotId, symbolId)
      .then((res) => {
        if (cancelled) return
        const step = cardStep(res, true)
        if (step === 'unavailable') {
          setPhase({ key, phase: 'error', error: res.reason ?? "This function can't be explained." })
          return
        }
        known.set(key, res.card!)
        setVersion((v) => v + 1)
        if (step === 'write') write(false)
        else setPhase({ key, phase: 'ready' })
      })
      .catch((e: Error) => { if (!cancelled) setPhase({ key, phase: 'error', error: e.message }) })
    return () => { cancelled = true }
  }, [key, snapshotId, symbolId, write])

  const current = phase.key === key ? phase : { key, phase: 'loading' as const }
  return {
    card: known.get(key),
    writing: current.phase === 'writing',
    loading: current.phase === 'loading',
    error: current.phase === 'error' ? current.error : null,
    regenerate: () => write(true),
  }
}

function Line({ path, line, onOpen, label }: {
  path: string
  line: number
  onOpen: (path: string, line: number) => void
  label?: string
}) {
  return (
    <button className="link-btn card-line" onClick={() => onOpen(path, line)} title={`Open ${path} at line ${line}`}>
      {label ?? `line ${line}`}
    </button>
  )
}

/** "What does this function do?": purpose, inputs, steps, output, why, side effects and an
 * example. Rows from the code show without an API key; AI rows are tagged and checked. */
export function FunctionCard({ snapshotId, symbolId, onOpen, onHighlight, onClose }: Props) {
  const { card, writing, loading, error, regenerate } = useCard(snapshotId, symbolId)
  const short = symbolId.split('::').pop()?.split('@')[0] ?? symbolId

  if (!card) {
    return (
      <section className="function-card" aria-label={`About ${short}`}>
        <header className="card-head">
          <h3><code>{short.split('.').pop()}()</code></h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close the function card"><CloseIcon /></button>
        </header>
        {loading && <p className="faint" role="status">Reading the function…</p>}
        {error && <p className="explain-error" role="status">{error}</p>}
      </section>
    )
  }

  const toLine = (line: number) => onOpen(card.path, line)
  const distinctCallers = new Map(card.why.callers.map((c) => [`${c.id}@${c.line}`, c]))

  return (
    <section className="function-card" aria-label={`About ${card.name}`}>
      <header className="card-head">
        <h3>
          <code>{card.name.split('.').pop()}()</code>
          <span className="faint">
            {card.kind === 'method' ? `method of ${card.name.split('.').slice(0, -1).join('.')}, ` : ''}
            lines {card.start}–{card.end}
          </span>
        </h3>
        <button className="icon-btn" onClick={onClose} aria-label="Close the function card"><CloseIcon /></button>
      </header>

      <dl className="card-rows">
        {card.purpose && (
          <div className="card-row">
            <dt>What it is for <SourceTag source={card.purpose.source} /></dt>
            <dd>{card.purpose.text}</dd>
          </div>
        )}

        <div className="card-row">
          <dt>What goes in</dt>
          <dd>
            {card.inputs.length === 0 ? <span className="faint">Nothing: it takes no inputs.</span> : (
              <table className="card-inputs">
                <thead><tr><th>Name</th><th>Type</th><th>Default</th><th>Meaning</th></tr></thead>
                <tbody>
                  {card.inputs.map((input) => (
                    <tr key={input.name}>
                      <td><code>{paramLabel(input)}</code></td>
                      <td>{input.type ? <code>{input.type}</code> : <span className="faint">not written</span>}</td>
                      <td>{input.default != null ? <code>{input.default}</code> : (
                        <span className="faint">{input.kind.startsWith('var_') ? 'optional, any number' : 'required'}</span>
                      )}</td>
                      <td>{input.meaning ?? <span className="faint">–</span>} <SourceTag source={input.source} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </dd>
        </div>

        {card.steps.length > 0 && (
          <div className="card-row">
            <dt>What happens <SourceTag source={card.steps[0].source} /></dt>
            <dd>
              <ol className="card-steps" onMouseLeave={() => onHighlight(null)}>
                {card.steps.map((step) => (
                  <li key={`${step.start}-${step.end}-${step.text}`}
                    onMouseEnter={() => onHighlight([step.start, step.end])}
                    onFocus={() => onHighlight([step.start, step.end])}>
                    <button className="link-btn card-line" onClick={() => toLine(step.start)}
                      title={`Show lines ${step.start}–${step.end}`}>
                      {step.start === step.end ? `line ${step.start}` : `lines ${step.start}–${step.end}`}
                    </button>
                    <span>{step.text}</span>
                  </li>
                ))}
              </ol>
            </dd>
          </div>
        )}

        <div className="card-row">
          <dt>What comes out <SourceTag source={card.output.source} /></dt>
          <dd>
            {card.output.meaning ? <>{card.output.meaning} </> : null}
            <span className={card.output.meaning ? 'faint' : undefined}>
              {card.output.type ? <>Type <code>{card.output.type}</code>. </> : null}
              {card.output.lines.length > 0 ? (
                <>Returns on {card.output.lines.map((n, i) => (
                  <span key={n}>{i > 0 ? ', ' : ''}<Line path={card.path} line={n} onOpen={onOpen} /></span>
                ))}.</>
              ) : card.output.meaning ? null : card.output.text}
            </span>
            {card.raises.length > 0 && (
              <div className="card-raises">
                Can stop with an error:{' '}
                {card.raises.map((r, i) => (
                  <span key={`${r.name}-${r.line}`}>{i > 0 ? ', ' : ''}<code>{r.name}</code>{' '}
                    (<Line path={card.path} line={r.line} onOpen={onOpen} />)</span>
                ))}
              </div>
            )}
          </dd>
        </div>

        <div className="card-row">
          <dt>Why it is needed <SourceTag source={card.why.source} /></dt>
          <dd>
            {card.why.text}
            {distinctCallers.size > 0 && (
              <ul className="card-callers">
                {[...distinctCallers.values()].map((c) => (
                  <li key={`${c.id}@${c.line}`}>
                    <Line path={c.path} line={c.line} onOpen={onOpen}
                      label={c.name === '<module>' ? `top of ${fileName(c.path)}` : `${c.name.split('.').pop()}()`} />
                    <span className="faint"> in {fileName(c.path)}, line {c.line}</span>
                    {c.tier === 'inferred' && <span className="faint" title="Matched by name only"> (best guess)</span>}
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </div>

        <div className="card-row">
          <dt>Side effects <SourceTag source="code" /></dt>
          <dd>
            {card.side_effects.length === 0 ? (
              <span className="faint">None found: no files, network, database, environment, programs or logs.</span>
            ) : (
              <ul className="card-effects">
                {card.side_effects.map((e) => (
                  <li key={`${e.kind}-${e.line}`}>
                    <span className={`effect effect-${e.kind}`}>{SIDE_EFFECT_LABELS[e.kind] ?? e.kind}</span>{' '}
                    <code>{e.call}</code> <Line path={card.path} line={e.line} onOpen={onOpen} />
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </div>

        {card.example && (
          <div className="card-row">
            <dt>Example <SourceTag source={card.example.source} /></dt>
            <dd>
              <pre className="card-example"><code>{card.example.code}</code></pre>
              <span className="faint">
                {card.example.from_test ? 'From a test: ' : 'From '}
                <Line path={card.example.path} line={card.example.start} onOpen={onOpen}
                  label={`${fileName(card.example.path)}, line ${card.example.start}`} />
              </span>
            </dd>
          </div>
        )}
      </dl>

      <div className="explain-note">
        {writing && <span role="status">Writing a plain-English explanation…</span>}
        {error && <span className="explain-error" role="status">Couldn't write the explanation: {error}</span>}
        {card.ai.status === 'ready' && (
          <>
            <span>AI rows were written from the code{card.ai.dropped ? ` (${plural(card.ai.dropped, 'answer')} failed the checks and ${card.ai.dropped === 1 ? 'was' : 'were'} left out)` : ''}.</span>
            <button className="link-btn" onClick={regenerate}>Regenerate</button>
            <details>
              <summary>What was sent</summary>
              <p>{card.ai.sent} Model: {card.ai.model}.</p>
            </details>
          </>
        )}
        {card.ai.status === 'trivial' && <span>Short enough to read directly, so no AI was used.</span>}
        {card.ai.status === 'off' && (
          <span>These rows come from the code. Set <code>GEMINI_API_KEY</code> and restart for a plain-English purpose and steps.</span>
        )}
        {card.ai.status === 'missing' && !writing && !error && (
          <button className="link-btn" onClick={() => regenerate()}>Write a plain-English explanation</button>
        )}
      </div>
    </section>
  )
}
