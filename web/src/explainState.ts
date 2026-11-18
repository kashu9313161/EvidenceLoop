import type { ExplainResponse } from './api'

/** What the file panel does with the server's answer about an AI explanation:
 * - show: there is one;
 * - write: AI is on but this file has none yet, so ask for it (only where allowed to spend);
 * - facts: no AI (no key, or not a code file), so the deterministic description stands alone. */
export type ExplainStep = 'show' | 'write' | 'facts'

export function explainStep(response: ExplainResponse, mayWrite: boolean): ExplainStep {
  if (!response.available) return 'facts'
  if (response.status === 'ready' && response.explanation) return 'show'
  if (response.status === 'missing' && mayWrite) return 'write'
  return 'facts'
}

/** The first sentence, for the one-line glance in lists. */
export function firstSentence(text: string): string {
  const match = text.match(/^.+?[.!?](?=\s|$)/s)
  return (match ? match[0] : text).trim()
}
