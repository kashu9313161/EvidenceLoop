/** What to say above a file that isn't analysed code (a README, a config file, an image),
 * so opening one from search or a link shows it instead of an empty page. */
export function plainFileNote(status: string, reason: string | null): string {
  if (status === 'indexed') {
    return 'Not code EvidenceLoop analyses: shown as text, and included in search.'
  }
  if (status === 'skipped') {
    return `Not analysed: ${reason ? reason.charAt(0).toLowerCase() + reason.slice(1) : 'unsupported file'}.`
  }
  if (status === 'failed') return `Couldn't be analysed: ${reason ?? 'unknown error'}.`
  return ''
}
