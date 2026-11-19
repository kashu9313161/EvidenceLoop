import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type Job, type RepoSummary, workspacePath } from '../api'
import { Logo, ThemeToggle } from '../components/bits'
import { Progress } from '../components/Progress'
import { jobOutcome } from '../jobOutcome'
import { plural } from '../util'

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

  // Acts on every job the page receives: the first response may already be finished.
  const receive = useCallback((next: Job) => {
    const outcome = jobOutcome(next)
    if (outcome.kind === 'open') {
      navigate(workspacePath(outcome.snapshotId))
    } else if (outcome.kind === 'failed') {
      setError(outcome.message)
      setJob(null)
    } else {
      setJob(next)
    }
  }, [navigate])

  useEffect(() => {
    if (!job || jobOutcome(job).kind !== 'running') return
    const timer = window.setTimeout(async () => {
      try {
        receive(await api.job(job.id))
      } catch (e) {
        setError((e as Error).message)
        setJob(null)
      }
    }, 450)
    return () => window.clearTimeout(timer)
  }, [job, receive])

  const start = async (run: () => Promise<Job>) => {
    setError(null)
    try {
      receive(await run())
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

  const remove = async (repo: RepoSummary) => {
    const label = repo.owner ? `${repo.owner}/${repo.name}` : repo.name
    const sure = window.confirm(
      `Remove ${label} from EvidenceLoop?

Its analysis, explanations and questions are deleted ` +
      'from this computer. Nothing on GitHub changes, and you can analyse it again any time.')
    if (!sure) return
    try {
      await api.removeRepo(repo.snapshot_id)
      setRepos((list) => (list ?? []).filter((r) => r.snapshot_id !== repo.snapshot_id))
    } catch (e) {
      setError((e as Error).message)
    }
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
                  <button type="button" key={example} title={`Analyse ${example} now`}
                    onClick={() => {
                      const value = `github.com/${example}`
                      setUrl(value)
                      start(() => api.analyzeUrl(value))  // an example should just run
                    }}>
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
            <div key={repo.snapshot_id} className="repo-item">
            <Link to={workspacePath(repo.snapshot_id)} className="repo-row">
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
            <button type="button" className="link-btn repo-remove" onClick={() => remove(repo)}
              aria-label={`Remove ${repo.name} from EvidenceLoop`}>Remove</button>
            </div>
          ))}
        </section>
      )}
    </div>
  )
}
