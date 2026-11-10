import { useEffect, useRef } from 'react'
import { FireworksShow } from './fireworksShow'

/** A full-window canvas that plays a fireworks show while it is mounted. */
export function Fireworks({ className }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!canvasRef.current) return
    const show = FireworksShow.onCanvas(canvasRef.current)
    show?.start()
    return () => show?.stop()
  }, [])

  return <canvas ref={canvasRef} className={className} />
}
