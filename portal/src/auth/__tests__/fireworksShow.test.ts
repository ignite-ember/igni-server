import { afterEach, describe, expect, it, vi } from 'vitest'
import { FireworksShow, Stage } from '../fireworksShow'

function fakeContext(): CanvasRenderingContext2D {
  return {
    fillRect: vi.fn(),
    beginPath: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
  } as unknown as CanvasRenderingContext2D
}

describe('FireworksShow', () => {
  afterEach(() => vi.restoreAllMocks())

  it('stops requesting frames once stopped', () => {
    // The loop used to reschedule itself forever with nothing able to
    // cancel it, so it outlived the page that started it.
    const queued: FrameRequestCallback[] = []
    let handles = 0
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      queued.push(cb)
      return ++handles
    })
    const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})

    const show = new FireworksShow(new Stage(fakeContext(), 800, 600, 'rgba(0,0,0,0.25)'))
    show.start()
    queued.shift()!(0)
    expect(queued).toHaveLength(1)

    show.stop()

    expect(cancel).toHaveBeenCalledWith(2)
  })
})
