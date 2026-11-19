import type { CardInput, Explanation, FileFacts, FunctionCardResponse, FunctionMetric, KeyPart, RowSource } from './api'

/** The words on a row's source tag, and what they mean on hover. */
export const SOURCE_TAGS: Record<RowSource, { label: string; title: string }> = {
  code: { label: 'from code', title: 'Worked out from the code itself' },
  docstring: { label: 'docstring', title: "The code's own description" },
  ai: { label: 'AI', title: 'Written by AI from the code; not checked' },
  'ai-checked': { label: 'AI, checked', title: 'Written by AI and checked against the code' },
}

export interface FileRow {
  key: 'purpose' | 'inputs' | 'steps' | 'outputs' | 'why'
  label: string
  text: string
  source: RowSource
  steps?: KeyPart[]
}

const LABELS: Record<FileRow['key'], string> = {
  purpose: 'What it is for',
  inputs: 'What goes in',
  steps: 'What happens',
  outputs: 'What comes out',
  why: 'Why it is needed',
}

/** The five rows for a file: the AI's answer where there is one, otherwise the code's. */
export function fileRows(explanation: Explanation | undefined, facts: FileFacts | null | undefined): FileRow[] {
  const rows: FileRow[] = []
  const add = (key: FileRow['key'], ai: string | undefined, fallback: { text: string; source: RowSource } | null | undefined) => {
    if (ai) rows.push({ key, label: LABELS[key], text: ai, source: 'ai' })
    else if (fallback?.text) rows.push({ key, label: LABELS[key], text: fallback.text, source: fallback.source })
  }
  add('purpose', explanation?.purpose, facts?.purpose)
  add('inputs', explanation?.inputs, facts?.inputs)
  const steps = explanation?.steps?.length ? explanation.steps : facts?.steps ?? []
  if (steps.length) {
    rows.push({ key: 'steps', label: LABELS.steps, text: '', source: steps[0].source, steps })
  }
  add('outputs', explanation?.outputs, facts?.outputs)
  add('why', explanation?.why, facts?.why)
  return rows
}

/** What the card does with the server's answer: show it, or ask for the AI part (only where
 * the view may spend). An unavailable card shows its reason. */
export function cardStep(response: FunctionCardResponse, mayWrite: boolean): 'show' | 'write' | 'unavailable' {
  if (response.status === 'unavailable' || !response.card) return 'unavailable'
  if (response.status === 'missing' && mayWrite) return 'write'
  return 'show'
}

/** `*args`, `**options`, or the plain name. */
export function paramLabel(input: Pick<CardInput, 'name' | 'kind'>): string {
  if (input.kind === 'var_positional') return `*${input.name}`
  if (input.kind === 'var_keyword') return `**${input.name}`
  return input.name
}

/** Which function's card to show for an opened line: the function starting there, or the one
 * already shown while the line stays inside it (a step or a return line was clicked). */
export function cardFor(metrics: FunctionMetric[], line: number | null, current: string | null): string | null {
  if (line == null) return null
  const starting = metrics.find((f) => f.start === line && f.id)
  if (starting) return starting.id
  const shown = metrics.find((f) => f.id === current)
  if (shown && line >= shown.start && line <= shown.end) return shown.id
  return null
}

export const SIDE_EFFECT_LABELS: Record<string, string> = {
  file: 'Reads or writes files',
  network: 'Uses the network',
  database: 'Uses a database',
  env: 'Reads environment variables',
  subprocess: 'Runs another program',
  log: 'Logs or prints',
}
