import type { AskResult, SearchResult } from './api'

/** One row in the command box's dropdown. */
export interface CommandOption {
  key: string
  kind: 'hit' | 'more' | 'file' | 'symbol' | 'text' | 'ask'
  group: string
  title: string
  detail: string
  path?: string
  line?: number
  snippet?: string
}

const MAX_HITS = 3

const fileName = (path: string) => path.split('/').pop() ?? path

/** Reads like a question for the AI rather than a name to find. */
export function looksLikeQuestion(text: string): boolean {
  const t = text.trim()
  return t.includes('?') || t.split(/\s+/).length >= 4
}

/** The dropdown's rows, in order: a lookup's answer (when it found something), files,
 * functions and text matches, and "Ask AI", which moves to the top for open questions. */
export function commandOptions(text: string, search: SearchResult | null,
  lookup: AskResult | null): CommandOption[] {
  const q = text.trim()
  if (!q) return []
  const out: CommandOption[] = []

  const hits = lookup && lookup.kind === 'lookup' ? lookup.claims : []
  if (lookup && hits.length > 0) {
    const byId = new Map(lookup.evidence.map((e) => [e.id, e]))
    hits.slice(0, MAX_HITS).forEach((claim, i) => {
      const item = byId.get(claim.evidence_ids[0])
      out.push({ key: `hit:${i}`, kind: 'hit', group: 'Answer', title: claim.text,
        detail: item ? `${item.path}, line ${item.start}` : '', path: item?.path, line: item?.start })
    })
    const total = hits.length + lookup.more
    if (total > MAX_HITS) {
      out.push({ key: 'more', kind: 'more', group: 'Answer', title: `See all ${total} in the chat`, detail: '' })
    }
  }

  for (const path of search?.files.slice(0, 6) ?? []) {
    out.push({ key: `f:${path}`, kind: 'file', group: 'Files', title: fileName(path), detail: path, path })
  }
  for (const s of search?.symbols.slice(0, 8) ?? []) {
    out.push({ key: `s:${s.path}:${s.start}`, kind: 'symbol', group: 'Classes and functions', title: s.name,
      detail: `${s.path}, line ${s.start}`, path: s.path, line: s.start })
  }
  for (const c of search?.content.slice(0, 6) ?? []) {
    out.push({ key: `c:${c.relative_path}`, kind: 'text', group: 'Text matches', title: fileName(c.relative_path),
      detail: c.relative_path, path: c.relative_path, snippet: c.snippet })
  }

  const ask: CommandOption = { key: 'ask', kind: 'ask', group: 'Ask AI', title: `Ask AI: “${q}”`,
    detail: 'Answered with code citations, each one checked' }
  return looksLikeQuestion(q) && hits.length === 0 ? [ask, ...out] : [...out, ask]
}
