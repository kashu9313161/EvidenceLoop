import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type Job, type RepoSummary, workspacePath } from '../api'
import { Logo, ThemeToggle } from '../components/bits'
import { plural } from '../util'

const STEPS = [
  { stage: 'fetch', label: 'Fetch the repository and check every file' },
  { stage: 'parse', label: 'Read and parse the code' },
  { stage: 'graph', label: 'Map how the files connect' },
  { stage: 'insights', label: 'Look for problems' },
]
const ORDER: Record<string, number> = { queued: -1, fetch: 0, parse: 1, measure: 1, graph: 2, insights: 3, done: 4 }

function Progress({ job }: { job: Job }) {
  const current = ORDER[job.stage] ?? 0
  return (
    <div className="progress" aria-live="polite">
      <h2>Analysing {job.source.replace(/^https:\/\/github\.com\//, '')}</h2>
      <span className="faint" style={{ fontSize: 14 }}>
        Nothing from the repository is run. It's read and parsed only.
      </span>
      <ol>
        {STEPS.map((step, index) => {
          const state = index < current ? 'done' : index === current ? 'active' : ''
          const counting = index === current && job.total > 0
          return (
            <li key={step.stage} className={state}>
              <span className="dot" aria-hidden="true">{index < current ? '✓' : index + 1}</span>
              <span>{step.label}</span>
              <span className="count">{counting ? `${job.done} of ${job.total}` : ''}</span>
            </li>
          )
        })}
      </ol>
      {job.message && current < 4 && <div className="detail">{job.message}</div>}
    </div>
  )
}

export function Home() {
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [repos, setRepos] = useState<RepoSummary[] | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    api.repos().then(setRepos).catch(() => setRepos([]))
  }, [])

  useEffect(() => {
    if (!job || job.stage === 'done' || job.stage === 'error') return
    const timer = window.setTimeout(async () => {
      try {
        const next = await api.job(job.id)
        if (next.stage === 'done' && next.snapshot_id) {
          navigate(workspacePath(next.snapshot_id))
          return
        }
        if (next.stage === 'error') {
          setError(next.error ?? 'The analysis failed.')
          setJob(null)
          return
        }
        setJob(next)
      } catch (e) {
        setError((e as Error).message)
        setJob(null)
      }
    }, 450)
    return () => window.clearTimeout(timer)
  }, [job, navigate])

  const start = async (run: () => Promise<Job>) => {
    setError(null)
    try {
      setJob(await run())
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    const value = url.trim()
    if (!value) {
      setError('Paste a GitHub repository address, for example github.com/pallets/flask.')
      return
    }
    start(() => api.analyzeUrl(value))
  }

  const takeFile = (file: File | undefined) => {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.zip')) {
      setError(`${file.name} isn't a ZIP file. Zip the repository folder and drop that.`)
      return
    }
    start(() => api.upload(file))
  }

  return (
    <div className="home">
      <header className="home-bar">
        <Link to="/" className="wordmark"><Logo /> EvidenceLoop</Link>
        <ThemeToggle />
      </header>

      <section className="hero">
        <h1>Understand a codebase before you change it.</h1>
        <p>
          Give it a GitHub repository. EvidenceLoop reads every file, maps how they connect, and
          points out what needs fixing, with the exact lines as proof.
        </p>

        {job ? (
          <Progress job={job} />
        ) : (
          <form
            className={`analyze ${dragging ? 'dragging' : ''}`}
            onSubmit={submit}
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); takeFile(e.dataTransfer.files[0]) }}
          >
            <div className="analyze-row">
              <input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="github.com/owner/repository"
                aria-label="GitHub repository address"
                autoFocus
                spellCheck={false}
              />
              <button className="btn btn-primary" type="submit">Analyse</button>
            </div>
            <div className="analyze-foot">
              <span>
                {dragging ? 'Drop the ZIP to analyse it' : 'Or drop a ZIP of any project here, or '}
                {!dragging && (
                  <button type="button" className="link-btn" onClick={() => fileInput.current?.click()}>
                    choose a file
                  </button>
                )}
              </span>
              <span className="examples">
                Try
                {['pallets/itsdangerous', 'pallets/click'].map((example) => (
                  <button type="button" key={example} onClick={() => setUrl(`github.com/${example}`)}>
                    {example}
                  </button>
                ))}
              </span>
            </div>
            <input ref={fileInput} type="file" accept=".zip" hidden
              onChange={(e) => takeFile(e.target.files?.[0])} />
          </form>
        )}
        {error && <p className="form-error" role="alert">{error}</p>}
      </section>

      {repos && repos.length > 0 && (
        <section className="repos" aria-label="Analysed repositories">
          <h2>Your repositories</h2>
          {repos.map((repo) => (
            <Link key={repo.snapshot_id} to={workspacePath(repo.snapshot_id)} className="repo-row">
              <span className="name">
                {repo.owner && <span className="owner">{repo.owner} / </span>}
                {repo.name}
              </span>
              <span className="headline">
                {repo.headline ?? 'Not analysed yet. Open it to run the analysis.'}
              </span>
              <span className="side">
                {repo.totals ? (
                  <>
                    <span>{plural(repo.totals.insights, 'issue')}</span>
                    <span>{plural(repo.totals.source_files, 'source file')}</span>
                  </>
                ) : (
                  <span>{plural(repo.files, 'file')}</span>
                )}
                {repo.commit && <span>commit {repo.commit.slice(0, 7)}</span>}
              </span>
            </Link>
          ))}
        </section>
      )}
    </div>
  )
}
