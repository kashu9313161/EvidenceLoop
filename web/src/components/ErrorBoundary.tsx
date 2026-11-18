import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

/** Shows what broke instead of a blank page, with a way back. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('EvidenceLoop view crashed', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="centered">
        <div>
          <h1 className="lede" style={{ margin: '0 auto 12px' }}>This view hit a problem.</h1>
          <p>{this.state.error.message}</p>
          <p>
            <button className="btn" onClick={() => window.location.reload()}>Reload</button>{' '}
            <a className="btn btn-quiet" href="/">Back to your repositories</a>
          </p>
        </div>
      </div>
    )
  }
}
