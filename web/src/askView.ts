import type { AskClaim, AskResult } from './api'

export const STATUS_LABEL = { verified: 'Verified', insufficient: 'Not enough evidence', rejected: 'Rejected' }

/** The files an answer cites, for "Show in map". Rejected claims don't count: their
 * citations failed. Pass a claim to get only its files. */
export function citedPaths(result: AskResult, claim?: AskClaim): string[] {
  const byId = new Map(result.evidence.map((e) => [e.id, e]))
  const claims = claim ? [claim] : result.claims.filter((c) => c.status !== 'rejected')
  const paths = claims.flatMap((c) => c.evidence_ids.map((id) => byId.get(id)?.path))
  return [...new Set(paths.filter((p): p is string => !!p))]
}

/** One line on what answering cost. */
export function usageLine(result: AskResult): string {
  const { usage } = result
  if (result.kind === 'lookup' || usage.calls === 0) {
    return result.kind === 'lookup' ? 'Answered from the index. No AI was used.' : 'No AI calls were made.'
  }
  const tokens = `${usage.input_tokens.toLocaleString()} tokens in, ${usage.output_tokens.toLocaleString()} out`
  const calls = `${usage.calls} AI call${usage.calls === 1 ? '' : 's'}`
  const cost = usage.cost_usd == null
    ? 'cost unknown for this model'
    : `about $${usage.cost_usd < 0.01 ? usage.cost_usd.toFixed(4) : usage.cost_usd.toFixed(2)} at list price`
  return `${calls}, ${tokens}, ${cost}.`
}

/** Counts for the summary line: "2 verified, 1 rejected". */
export function statusCounts(claims: AskClaim[]): string {
  const order: AskClaim['status'][] = ['verified', 'insufficient', 'rejected']
  const words = { verified: 'verified', insufficient: 'not enough evidence', rejected: 'rejected' }
  return order
    .map((s) => [s, claims.filter((c) => c.status === s).length] as const)
    .filter(([, n]) => n > 0)
    .map(([s, n]) => `${n} ${words[s]}`)
    .join(', ')
}
