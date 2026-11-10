import { Stack, Text } from '@primer/react'

import type { TeamMember } from '../../types'
import { formatTokens } from '../../utils/formatTokens'

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <Stack className="team-stat" padding="normal" gap="condensed">
      <Text size="small" className="team-muted">
        {label}
      </Text>
      <Text size="medium" weight="semibold">
        {value}
      </Text>
    </Stack>
  )
}

export function MemberStats({ members }: { members: TeamMember[] }) {
  const active = members.filter((m) => m.status === 'active').length
  const invited = members.filter((m) => m.status === 'invited').length
  const admins = members.filter((m) => m.role === 'admin' && m.status === 'active').length
  const totalTokens = members.reduce((sum, m) => sum + m.total_tokens, 0)

  return (
    <Stack direction="horizontal" gap="normal" wrap="wrap">
      <Stat label="Active members" value={active} />
      <Stat label="Pending invites" value={invited} />
      <Stat label="Admins" value={admins} />
      <Stat label="Org tokens (30d)" value={formatTokens(totalTokens)} />
    </Stack>
  )
}
