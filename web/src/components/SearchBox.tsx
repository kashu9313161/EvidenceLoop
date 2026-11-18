import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type SearchResult } from '../api'
import { fileName } from '../util'

interface Props {
  snapshotId: string
  onPick: (path: string, line?: number) => void
}

interface Option {
  key: string
  path: string
  line?: number
  title: string
  detail: string
  group: string
  snippet?: string
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

export function SearchBox({ snapshotId, onPick }: Props) {
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<SearchResult | null>(null)
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
    if (!q) {
      setResult(null)
      return
    }
    const timer = window.setTimeout(() => {
      api.search(snapshotId, q).then(setResult).catch(() => setResult(null))
    }, 160)
    return () => window.clearTimeout(timer)
  }, [text, snapshotId])

  const options = useMemo<Option[]>(() => {
    if (!result) return []
    const out: Option[] = []
    for (const path of result.files.slice(0, 6)) {
      out.push({ key: `f:${path}`, path, title: fileName(path), detail: path, group: 'Files' })
    }
    for (const s of result.symbols.slice(0, 8)) {
      out.push({ key: `s:${s.path}:${s.start}`, path: s.path, line: s.start, title: s.name,
        detail: `${s.path}, line ${s.start}`, group: 'Classes and functions' })
    }
    for (const c of result.content.slice(0, 6)) {
      out.push({ key: `c:${c.relative_path}`, path: c.relative_path, title: fileName(c.relative_path),
        detail: c.relative_path, group: 'Text matches', snippet: c.snippet })
    }
    return out
  }, [result])

  useEffect(() => setActive(0), [options])

  const pick = (option: Option) => {
    onPick(option.path, option.line)
    setOpen(false)
    setText('')
    input.current?.blur()
  }

  let lastGroup = ''
  return (
    <div className="search" onBlur={(e) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false)
    }}>
      <input
        ref={input}
        type="search"
        value={text}
        placeholder="Search files, functions, text"
        aria-label="Search this repository"
        aria-expanded={open && !!text}
        aria-controls="search-results"
        role="combobox"
        onFocus={() => setOpen(true)}
        onChange={(e) => { setText(e.target.value); setOpen(true) }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, options.length - 1)) }
          if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
          if (e.key === 'Enter' && options[active]) pick(options[active])
          if (e.key === 'Escape') { setOpen(false); input.current?.blur() }
        }}
      />
      {!text && <span className="kbd" aria-hidden="true">{isMac ? '⌘K' : 'Ctrl K'}</span>}
      {open && text.trim() && (
        <div className="search-results" id="search-results" role="listbox">
          {result && options.length === 0 && <div className="empty">Nothing matches “{text.trim()}”.</div>}
          {!result && <div className="empty">Searching…</div>}
          {options.map((option, index) => {
            const header = option.group !== lastGroup ? <h3>{option.group}</h3> : null
            lastGroup = option.group
            return (
              <div key={option.key}>
                {header}
                <button role="option" aria-selected={index === active} onMouseEnter={() => setActive(index)}
                  onMouseDown={(e) => e.preventDefault()} onClick={() => pick(option)}>
                  {option.title}
                  <small>{option.snippet ? <Snippet text={option.snippet} /> : option.detail}</small>
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
