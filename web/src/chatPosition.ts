/** Where the movable chat button sits, and where the chat opens next to it. */

export interface Point {
  x: number
  y: number
}

export interface Size {
  width: number
  height: number
}

export const BUTTON = 52
const MARGIN = 8

/** Keep the button fully inside the window. */
export function clampButton(p: Point, view: Size): Point {
  return {
    x: Math.round(Math.min(Math.max(p.x, MARGIN), view.width - BUTTON - MARGIN)),
    y: Math.round(Math.min(Math.max(p.y, MARGIN), view.height - BUTTON - MARGIN)),
  }
}

/** The chat panel opens from the button toward the middle of the window: a button on the
 * right opens it leftwards, one near the top opens it downwards. It always stays on screen. */
export function panelPlacement(button: Point, view: Size): { left: number; top: number; width: number; height: number } {
  const width = Math.min(460, view.width - 2 * MARGIN)
  const height = Math.min(680, view.height - 2 * MARGIN)
  const onRight = button.x + BUTTON / 2 > view.width / 2
  const onBottom = button.y + BUTTON / 2 > view.height / 2
  const left = onRight ? button.x + BUTTON - width : button.x
  const top = onBottom ? button.y + BUTTON - height : button.y
  return {
    left: Math.round(Math.min(Math.max(left, MARGIN), view.width - width - MARGIN)),
    top: Math.round(Math.min(Math.max(top, MARGIN), view.height - height - MARGIN)),
    width,
    height,
  }
}

/** A saved position, or null when there is none or it can't be read. */
export function parsePosition(raw: string | null): Point | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw)
    return Number.isFinite(value?.x) && Number.isFinite(value?.y) ? { x: value.x, y: value.y } : null
  } catch {
    return null
  }
}
