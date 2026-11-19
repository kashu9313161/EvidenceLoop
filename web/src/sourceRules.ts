import type { SourceRules } from './api'

/** Why a file in an uploaded folder isn't sent, or null if it is. The same rules as the server
 * (`ingestion/rules.py`, shipped by GET /api/config); the server checks everything again. */
export function skipReason(path: string, size: number, rules: SourceRules): string | null {
  const parts = path.split('/')
  const folders = parts.slice(0, -1)
  const name = parts[parts.length - 1] ?? ''
  const dot = name.lastIndexOf('.')
  const extension = dot > 0 ? name.slice(dot).toLowerCase() : ''

  for (const folder of folders) {
    for (const [reason, names] of Object.entries(rules.ignored_directories)) {
      if (names.includes(folder)) return reason
    }
  }
  if (folders.includes(rules.vendor_directory) && rules.vendor_extensions.includes(extension)) {
    return 'Installed dependencies'
  }
  if (rules.ignored_file_names.includes(name)) return 'Ignored file'
  for (const [reason, extensions] of Object.entries(rules.skipped_extensions)) {
    if (extensions.includes(extension)) return reason
  }
  if (extension === '.svg' && size > rules.svg_skip_bytes) return 'Media or document'
  if (!rules.supported_extensions.includes(extension)) return 'Unsupported file type'
  if (size > rules.max_file_bytes) return 'Too large'
  return null
}

export interface FolderPick {
  name: string
  keep: { path: string; file: File }[]
  skipped: number
  /** Skipped files grouped by why, most first. */
  reasons: { reason: string; files: number }[]
}

/** Split a folder chosen with <input webkitdirectory> into what is sent and what isn't. Paths
 * are relative to the chosen folder's parent, so they start with the folder's own name. */
export function pickFolder(files: { webkitRelativePath: string; name: string; size: number }[],
  rules: SourceRules): FolderPick {
  const keep: { path: string; file: File }[] = []
  const counts = new Map<string, number>()
  let name = ''
  for (const file of files) {
    const path = file.webkitRelativePath || file.name
    if (!name) name = path.split('/')[0] ?? ''
    // The chosen folder's own name isn't part of the project's paths.
    const inside = path.includes('/') ? path.slice(path.indexOf('/') + 1) : path
    const reason = skipReason(inside, file.size, rules)
    if (reason) counts.set(reason, (counts.get(reason) ?? 0) + 1)
    else keep.push({ path, file: file as File })
  }
  const reasons = [...counts.entries()].map(([reason, n]) => ({ reason, files: n }))
    .sort((a, b) => b.files - a.files || a.reason.localeCompare(b.reason))
  return { name, keep, skipped: files.length - keep.length, reasons }
}
