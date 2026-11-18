import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type AskResult, type SearchResult } from '../api'
import { commandOptions, type CommandOption } from '../commandOptions'
import { Rich } from './bits'

interface Props {
  snapshotId: string
  onPick: (path: string, line?: number) => void
  onAsk: (question: string) => void
}

function Snippet({ text }: { text: string }) {
  const parts = text.replace(/\s+/g, ' ').split(/(\[\[.*?\]\])/g)
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith('[[') ? <mark key={i}>{part.slice(2, -2)}</mark> : <span key={i}>{part}</span>,
      )}
    </>
  )
}

/** One box for everything quick: files, functions and text as you type; lookups ("where is",
 * "what calls", "which files use") answered in place with no AI; and "Ask AI" to send anything
 * else to the chat. Ctrl+K, ⌘K or / focuses it. */
export function CommandBox({ snapshotId, onPick, onAsk }: Props) {
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [found, setFound] = useState<{ text: string; search: SearchResult | null; lookup: AskResult | null } | null>(null)
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement | null>(null)
  const isMac = typeof navigator !== 'undefined' && /Mac/.test(navigator.platform)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest('input, textarea, [contenteditable]')
      if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || (e.key === '/' && !typing)) {
        e.preventDefault()
        input.current?.focus()
        setOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const q = text.trim()
    if (!q) return
    let cancelled = false
    const timer = window.setTimeout(() => {
      // A lookup needs a verb and a name, so one word is only a search.
      const lookup = q.includes(' ')
        ? api.lookup(snapshotId, q).then((r) => (r.kind === 'lookup' ? r as AskResult : null)).catch(() => null)
        : Promise.resolve(null)
      const search = api.search(snapshotId, q).catch(() => null)
      Promise.all([search, lookup]).then(([s, l]) => {
        if (!cancelled) setFound({ text: q, search: s, lookup: l })
      })
    }, 160)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [text, snapshotId])

  const current = found && found.text === text.trim() ? found : null
  const options = useMemo(() => commandOptions(text, current?.search ?? null, current?.lookup ?? null),
    [text, current])
  const selected = Math.min(active, Math.max(options.length - 1, 0))

  const pick = (option: CommandOption) => {
    if (option.kind === 'ask' || option.kind === 'more') onAsk(text.trim())
    else if (option.path) onPick(option.path, option.line)
    setOpen(false)
    setText('')
    setActive(0)
    input.current?.blur()
  }

  return (
    <div className="search" onBlur={(e) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false)
    }}>
      <input
        ref={input}
        type="search"
        value={text}
        maxLength={500}
        placeholder="Search or ask: where is…, what calls…"
        aria-label="Search or ask about this repository"
        aria-expanded={open && !!text}
        aria-controls="search-results"
        role="combobox"
        onFocus={() => setOpen(true)}
        onChange={(e) => { setText(e.target.value); setOpen(true); setActive(0) }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setActive(Math.min(selected + 1, options.length - 1)) }
          if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(selected - 1, 0)) }
          if (e.key === 'Enter' && options[selected]) pick(options[selected])
          if (e.key === 'Escape') { setOpen(false); input.current?.blur() }
        }}
      />
      {!text && <span className="kbd" aria-hidden="true">{isMac ? '⌘K' : 'Ctrl K'}</span>}
      {open && text.trim() && (
        <div className="search-results" id="search-results" role="listbox">
          {!current && <div className="empty">Searching…</div>}
          {options.map((option, index) => {
            const header = index === 0 || options[index - 1].group !== option.group ? (
              <h3>
                {option.group}
                {option.group === 'Answer' && current?.lookup && (
                  <span className="answer-summary"> · <Rich text={current.lookup.summary} /> No AI used.</span>
                )}
              </h3>
            ) : null
            return (
              <div key={option.key}>
                {header}
                <button role="option" aria-selected={index === selected} className={`opt-${option.kind}`}
                  onMouseEnter={() => setActive(index)}
                  onMouseDown={(e) => e.preventDefault()} onClick={() => pick(option)}>
                  {option.kind === 'hit' ? <Rich text={option.title} /> : option.title}
                  {option.detail || option.snippet ? (
                    <small>{option.snippet ? <Snippet text={option.snippet} /> : option.detail}</small>
                  ) : null}
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
