import { useCallback, useEffect, useRef, useState } from 'react'
import type { AiStatus, AskResult } from '../api'
import { clampButton, panelPlacement, parsePosition, type Point } from '../chatPosition'
import { AnswerView, type AnswerActions } from './Answer'
import { CloseIcon } from './bits'

export interface ChatMessage {
  id: string
  question: string
  result?: AskResult
  error?: string
}

const PROVIDER_NAMES: Record<string, string> = { gemini: 'Gemini', anthropic: 'Claude', fake: 'Demo' }

const POSITION_KEY = 'el-chat-position'
const KEY_STEP = 24

const viewport = () => ({ width: window.innerWidth, height: window.innerHeight })

/** Where the user has put the chat button (null: the default corner), kept between visits
 * and pulled back inside the window when it gets smaller. */
export function useChatPosition() {
  const [pos, setPos] = useState<Point | null>(() => {
    try {
      const saved = parsePosition(localStorage.getItem(POSITION_KEY))
      return saved && clampButton(saved, viewport())
    } catch {
      return null
    }
  })
  useEffect(() => {
    const onResize = () => setPos((p) => p && clampButton(p, viewport()))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  const move = useCallback((p: Point | null, save: boolean) => {
    const next = p && clampButton(p, viewport())
    setPos(next)
    if (!save) return
    try {
      if (next) localStorage.setItem(POSITION_KEY, JSON.stringify(next))
      else localStorage.removeItem(POSITION_KEY)
    } catch {
      /* private mode: the position lasts for this visit */
    }
  }, [])
  return { pos, move }
}

interface ButtonProps {
  busy: boolean
  shifted: boolean
  pos: Point | null
  onMove: (pos: Point | null, save: boolean) => void
  onClick: () => void
}

/** The chat button. Drag it anywhere; a click (or Enter) opens the chat. With the keyboard,
 * the arrow keys move it and Home puts it back in the corner. */
export function ChatButton({ busy, shifted, pos, onMove, onClick }: ButtonProps) {
  const drag = useRef<{ dx: number; dy: number; sx: number; sy: number; moved: boolean; at: Point } | null>(null)
  const justDragged = useRef(false)
  const [dragging, setDragging] = useState(false)
  const here = (el: HTMLElement): Point => {
    const r = el.getBoundingClientRect()
    return { x: r.left, y: r.top }
  }

  return (
    <button
      className={`chat-button ${shifted && !pos ? 'shifted' : ''} ${pos ? 'placed' : ''} ${dragging ? 'dragging' : ''}`}
      style={pos ? { left: pos.x, top: pos.y, right: 'auto', bottom: 'auto' } : undefined}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        const at = here(e.currentTarget)
        drag.current = { dx: e.clientX - at.x, dy: e.clientY - at.y, sx: e.clientX, sy: e.clientY, moved: false, at }
        e.currentTarget.setPointerCapture(e.pointerId)
      }}
      onPointerMove={(e) => {
        const d = drag.current
        if (!d) return
        // A few pixels of wobble during a click isn't a drag.
        if (!d.moved && Math.hypot(e.clientX - d.sx, e.clientY - d.sy) < 5) return
        if (!d.moved) setDragging(true)
        d.moved = true
        d.at = { x: e.clientX - d.dx, y: e.clientY - d.dy }
        onMove(d.at, false)
      }}
      onPointerUp={() => {
        const d = drag.current
        drag.current = null
        if (d?.moved) {
          justDragged.current = true
          setDragging(false)
          onMove(d.at, true)
        }
      }}
      onPointerCancel={() => { drag.current = null; setDragging(false) }}
      onClick={() => {
        if (justDragged.current) {
          justDragged.current = false  // the click that ends a drag doesn't open the chat
          return
        }
        onClick()
      }}
      onKeyDown={(e) => {
        const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key]
        if (step) {
          e.preventDefault()
          const from = pos ?? here(e.currentTarget)
          onMove({ x: from.x + step[0] * KEY_STEP, y: from.y + step[1] * KEY_STEP }, true)
        } else if (e.key === 'Home') {
          e.preventDefault()
          onMove(null, true)
        }
      }}
      aria-label={busy ? 'Ask AI (an answer is being written)' : 'Ask AI about this code'}
      title="Ask AI. Drag to move it anywhere.">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-4.9A8 8 0 1 1 21 12z" />
        <path d="M9 10.5h6M9 13.5h4" />
      </svg>
      {busy && <span className="chat-dot" aria-hidden="true" />}
    </button>
  )
}

interface PanelProps extends AnswerActions {
  repoName: string
  ai: AiStatus | null
  messages: ChatMessage[]
  examples: string[]
  onSend: (question: string) => void
  onMinimise: () => void
  onClose: () => void
}

export function ChatPanel({ repoName, ai, messages, examples, onSend, onMinimise, onClose, onOpenCode,
  onShowInMap }: PanelProps) {
  const [text, setText] = useState('')
  const latest = useRef<HTMLDivElement | null>(null)
  const field = useRef<HTMLInputElement | null>(null)

  // The newest question scrolls to the top when it is sent and again when its answer arrives
  // (only then is there enough below it to scroll), so answers are read from the summary down.
  const last = messages.at(-1)
  const lastDone = !!(last?.result || last?.error)
  useEffect(() => { latest.current?.scrollIntoView({ block: 'start' }) }, [messages.length, lastDone])
  useEffect(() => { field.current?.focus() }, [])

  const send = (question: string) => {
    const q = question.trim()
    if (!q) return
    onSend(q)
    setText('')
  }

  return (
    <section className="chat-panel" role="dialog" aria-label={`Ask about ${repoName}`}>
      <header className="chat-head">
        <div>
          <h2>Ask about {repoName}</h2>
          <div className="chat-chips">
            {ai === null ? <span className="chip-mini">Checking AI…</span>
              : ai.available ? (
                <>
                  <span className="chip-mini" title="Answers open questions">
                    {PROVIDER_NAMES[ai.provider] ?? ai.provider} · {ai.model}
                  </span>
                  <span className="chip-mini" title="Checks each claim against its cited code">
                    Judge: LLM{ai.judge.model && ai.judge.model !== ai.model ? ` (${ai.judge.model})` : ''}
                  </span>
                </>
              ) : <span className="chip-mini off" title={ai.reason}>AI off: set GEMINI_API_KEY</span>}
          </div>
        </div>
        <div className="chat-actions">
          <button className="icon-btn" onClick={onMinimise} aria-label="Minimise the chat" title="Minimise">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" aria-hidden="true"><path d="M5 12h14" /></svg>
          </button>
          <button className="icon-btn" onClick={onClose} aria-label="Close the chat" title="Close"><CloseIcon /></button>
        </div>
      </header>

      <div className="chat-log" aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>
              Ask where something is, what calls it, or how a part works. Lookups are answered from the
              index instantly; open questions are answered by AI, and every claim is checked against the
              code it cites.
            </p>
            <div className="chat-examples">
              {examples.map((q) => (
                <button key={q} className="chip" onClick={() => send(q)}>{q}</button>
              ))}
            </div>
            <p className="faint">For now each question is answered on its own, without memory of earlier ones.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={m.id} className="chat-turn" ref={i === messages.length - 1 ? latest : undefined}>
            <p className="chat-question">{m.question}</p>
            <div className="chat-answer">
              {m.result ? <AnswerView result={m.result} onOpenCode={onOpenCode} onShowInMap={onShowInMap} />
                : m.error ? <p className="explain-error">{m.error}</p>
                  : <p className="ask-wait" role="status">Finding evidence in the code and checking each claim…</p>}
            </div>
          </div>
        ))}
      </div>

      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(text) }}>
        <input ref={field} type="text" value={text} maxLength={500} onChange={(e) => setText(e.target.value)}
          placeholder="Ask a question about this code" aria-label="Your question" />
        <button type="submit" className="btn" disabled={!text.trim()}>Ask</button>
      </form>
    </section>
  )
}
