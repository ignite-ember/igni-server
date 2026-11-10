import { PageHeader, Stack } from '@primer/react'
import { SkeletonText } from '@primer/react/experimental'

const PLACEHOLDER_ROWS = Array.from({ length: 4 }, (_, i) => i)
const CELL_WIDTHS = [160, 60, 50, 40, 60, 80, 80]

/** The page's shape while the member list and the signed-in user load. */
export function TeamPageSkeleton() {
  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Team</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <SkeletonText size="bodyMedium" maxWidth={100} />
        </PageHeader.Description>
      </PageHeader>
      <Stack direction="horizontal" gap="normal" wrap="wrap">
        {PLACEHOLDER_ROWS.map((i) => (
          <Stack key={i} className="team-stat" padding="normal" gap="condensed">
            <SkeletonText size="bodySmall" maxWidth={100} />
            <SkeletonText size="bodyMedium" maxWidth={40} />
          </Stack>
        ))}
      </Stack>
      <Stack gap="none">
        {PLACEHOLDER_ROWS.map((i) => (
          <Stack key={i} direction="horizontal" gap="normal" padding="normal" className="team-skeleton-row">
            {CELL_WIDTHS.map((width, cell) => (
              <SkeletonText key={cell} size={cell === 0 ? 'bodyMedium' : 'bodySmall'} maxWidth={width} />
            ))}
          </Stack>
        ))}
      </Stack>
    </Stack>
  )
}
