/**
 * A short canvas fireworks show: a handful of rockets climb, burst into
 * sparks, and the sparks fall and fade. Pure drawing — no React — so the
 * component that hosts it only has to start it and stop it.
 */

const SPARK_COLORS = ['#F97316', '#DC2626', '#FBBF24', '#f85149', '#f0883e', '#FF6B35']
const FLASH_COLOR = '#FBBF24'
const SPARKS_PER_BURST = 80
const ROCKET_COUNT = 6
const ROCKET_STAGGER_FRAMES = 25

function randomBetween(min: number, max: number): number {
  return min + Math.random() * (max - min)
}

/** A point moving under gravity: position plus velocity, advanced one frame at a time. */
class Motion {
  x: number
  y: number
  vx: number
  vy: number

  constructor(x: number, y: number, vx: number, vy: number) {
    this.x = x
    this.y = y
    this.vx = vx
    this.vy = vy
  }

  advance(gravity: number): void {
    this.x += this.vx
    this.y += this.vy
    this.vy += gravity
  }
}

/** The canvas a show draws on, with its size and the colour that fades the last frame. */
export class Stage {
  readonly ctx: CanvasRenderingContext2D
  readonly width: number
  readonly height: number
  private readonly trailColor: string

  constructor(ctx: CanvasRenderingContext2D, width: number, height: number, trailColor: string) {
    this.ctx = ctx
    this.width = width
    this.height = height
    this.trailColor = trailColor
  }

  /** Paint the trail colour over everything so earlier frames fade out. */
  fade(): void {
    this.ctx.fillStyle = this.trailColor
    this.ctx.fillRect(0, 0, this.width, this.height)
  }

  dot(x: number, y: number, radius: number, color: string, alpha: number): void {
    const { ctx } = this
    ctx.globalAlpha = alpha
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(x, y, radius, 0, Math.PI * 2)
    ctx.fill()
  }
}

class Spark {
  private readonly motion: Motion
  private readonly radius = randomBetween(1.5, 3.5)
  private readonly color = SPARK_COLORS[Math.floor(Math.random() * SPARK_COLORS.length)]
  private readonly lifespan = randomBetween(80, 120)
  private remaining = this.lifespan

  constructor(x: number, y: number) {
    const angle = Math.random() * Math.PI * 2
    const speed = randomBetween(2, 7)
    this.motion = new Motion(x, y, Math.cos(angle) * speed, Math.sin(angle) * speed)
  }

  get alive(): boolean {
    return this.remaining > 0
  }

  step(stage: Stage): void {
    this.remaining--
    this.motion.advance(0.1)
    this.motion.vx *= 0.98
    const age = 1 - this.remaining / this.lifespan
    const color = age < 0.15 ? FLASH_COLOR : this.color
    stage.dot(this.motion.x, this.motion.y, this.radius * (1 - age * 0.5), color, 1 - age)
  }
}

class Rocket {
  private readonly motion: Motion
  private readonly targetY: number
  private readonly launchFrame: number
  private exploded = false

  constructor(width: number, height: number, launchFrame: number) {
    this.launchFrame = launchFrame
    const x = width * 0.15 + Math.random() * width * 0.7
    this.motion = new Motion(x, height, 0, randomBetween(-16, -12))
    this.targetY = height * 0.1 + Math.random() * height * 0.3
  }

  /** Advance one frame; returns the sparks of its burst on the frame it bursts. */
  step(stage: Stage, frame: number): Spark[] {
    if (frame < this.launchFrame || this.exploded) return []
    const { motion } = this
    motion.advance(0.15)
    stage.dot(motion.x, motion.y, 3, FLASH_COLOR, 1)
    if (motion.y > this.targetY && motion.vy < 0) return []
    this.exploded = true
    return Array.from({ length: SPARKS_PER_BURST }, () => new Spark(motion.x, motion.y))
  }
}

/** The page background at quarter opacity, so each frame leaves a fading trail. */
function trailColorFor(background: string): string {
  const rgb = background.match(/\d+/g)
  return rgb ? `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.25)` : 'rgba(255,255,255,0.25)'
}

export class FireworksShow {
  private readonly rockets: Rocket[]
  private sparks: Spark[] = []
  private frame = 0
  private handle: number | null = null
  private readonly stage: Stage

  constructor(stage: Stage) {
    this.stage = stage
    this.rockets = Array.from(
      { length: ROCKET_COUNT },
      (_, i) => new Rocket(stage.width, stage.height, i * ROCKET_STAGGER_FRAMES),
    )
  }

  /** Size the canvas to the window and build a show for it; null without a 2D context. */
  static onCanvas(canvas: HTMLCanvasElement): FireworksShow | null {
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    canvas.width = window.innerWidth
    canvas.height = window.innerHeight
    const background = getComputedStyle(document.body).backgroundColor || 'rgb(255,255,255)'
    return new FireworksShow(
      new Stage(ctx, canvas.width, canvas.height, trailColorFor(background)),
    )
  }

  start(): void {
    if (this.handle === null) this.handle = requestAnimationFrame(this.tick)
  }

  stop(): void {
    if (this.handle !== null) cancelAnimationFrame(this.handle)
    this.handle = null
  }

  private readonly tick = (): void => {
    const { stage } = this
    stage.fade()
    for (const rocket of this.rockets) this.sparks.push(...rocket.step(stage, this.frame))
    this.sparks = this.sparks.filter((spark) => spark.alive)
    for (const spark of this.sparks) spark.step(stage)
    stage.ctx.globalAlpha = 1
    this.frame++
    this.handle = requestAnimationFrame(this.tick)
  }
}
