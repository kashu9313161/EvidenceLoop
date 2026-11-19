/** The parts of a DOM element scrollToLine needs (so tests can pass plain objects). */
export interface LineBox { offsetTop: number }
export interface Scroller { clientHeight: number; scrollTop: number }
type Observer = { observe(target: Scroller): void; disconnect(): void }
type ObserverCtor = new (callback: () => void) => Observer

/**
 * Put `line` a third of the way down its own scroller, and keep it there while the scroller
 * changes height for `settleMs` (a function card opening above the code shrinks it after the
 * first scroll). Returns a cleanup that stops watching.
 */
export function scrollToLine(line: LineBox, scroller: Scroller, settleMs = 1500,
  Observer: ObserverCtor | undefined = globalThis.ResizeObserver as ObserverCtor | undefined): () => void {
  const place = () => {
    // the scroller is position: relative, so offsetTop is measured from its top
    scroller.scrollTop = Math.max(0, line.offsetTop - scroller.clientHeight / 3)
  }
  place()
  if (!Observer) return () => {}
  const observer = new Observer(place)
  observer.observe(scroller)
  const timer = setTimeout(() => observer.disconnect(), settleMs)
  return () => { clearTimeout(timer); observer.disconnect() }
}
