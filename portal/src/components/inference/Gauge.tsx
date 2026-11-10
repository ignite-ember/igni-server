import { ProgressBar, Stack, Text } from '@primer/react'

/** A labelled bar with its value beside it, so a screen reader hears the number too. */
export function Gauge({
  label,
  readout,
  ratio,
  color,
  caption,
}: {
  label: string
  readout: string
  ratio: number
  color: string
  caption?: string
}) {
  return (
    <Stack gap="condensed" padding="none">
      <Stack direction="horizontal" justify="space-between" align="baseline" gap="condensed">
        <Text size="small" weight="semibold">
          {label}
        </Text>
        <Text size="small" className="inference-numeric">
          {readout}
        </Text>
      </Stack>
      <ProgressBar
        progress={Math.min(100, Math.max(0, ratio * 100))}
        bg={color}
        barSize="small"
        aria-label={`${label}: ${readout}`}
      />
      {caption && (
        <Text size="small" className="inference-muted">
          {caption}
        </Text>
      )}
    </Stack>
  )
}
