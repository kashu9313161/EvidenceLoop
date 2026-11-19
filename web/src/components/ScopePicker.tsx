import type { IngestPlan } from '../api'
import { formatBytes, plural } from '../util'

interface Props {
  plan: IngestPlan
  source: string
  onPick: (scope: string) => void
  onCancel: () => void
}

/** Shown when a repository has more source than one analysis takes: what the preflight found,
 * the folders with their source-file counts, and "analyse the most important files". */
export function ScopePicker({ plan, source, onPick, onCancel }: Props) {
  const name = source.replace(/^https:\/\/github\.com\//, '')
  const folders = plan.folders.filter((f) => !f.top_level_files)
  const topLevel = plan.folders.find((f) => f.top_level_files)
  const fits = (files: number, bytes: number) =>
    files <= plan.limits.files && bytes <= plan.limits.bytes
  return (
    <div className="progress scope-picker" aria-live="polite">
      <h2>{plan.scope && plan.scope !== '*' ? `${name}: ${plan.scope}/` : name} is too big to analyse at once</h2>
      <p className="scope-summary">{plan.summary}</p>
      <p className="faint" style={{ fontSize: 14, margin: '4px 0 12px' }}>
        Pick a folder to analyse, or let EvidenceLoop choose the most important{' '}
        {plan.limits.files.toLocaleString()} files (manifests and READMEs, entry points, then
        the files nearest the top).
      </p>
      {folders.length > 0 && (
        <ul className="scope-folders">
          {folders.map((folder) => (
            <li key={folder.folder}>
              <button type="button" onClick={() => onPick(folder.folder)}>
                <span className="nm">{folder.folder}/</span>
                <span className="faint">
                  {plural(folder.files, 'source file')}, {formatBytes(folder.bytes)}
                  {!fits(folder.files, folder.bytes) && ' (still big: you can pick inside it next)'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {topLevel && (
        <p className="faint" style={{ fontSize: 13.5 }}>
          Plus {plural(topLevel.files, 'file')} directly in {plan.scope && plan.scope !== '*' ? `${plan.scope}/` : 'the top folder'}.
        </p>
      )}
      <div className="scope-actions">
        <button type="button" className="btn btn-primary" onClick={() => onPick('*')}>
          Analyse the most important files
        </button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}
