import { Label, RelativeTime, Text } from '@primer/react'

import type { VmCommandRecord } from '../../../types'
import { commandFailed } from '../attention'
import { Facts, Rows, SectionCard } from '../SectionCard'
import { RECENT_LIMIT, type DashboardData } from '../useDashboardData'

type LabelVariant = 'secondary' | 'accent' | 'success' | 'danger'

/** Where a command stands, as a word and a colour. */
function commandState(c: VmCommandRecord): { word: string; variant: LabelVariant } {
  if (commandFailed(c)) return { word: 'Failed', variant: 'danger' }
  switch (c.state) {
    case 'finished':
      return { word: 'Done', variant: 'success' }
    case 'running':
    case 'handed_out':
      return { word: 'Running', variant: 'accent' }
    case 'queued':
      return { word: 'Queued', variant: 'secondary' }
    case 'cancelled':
      return { word: 'Cancelled', variant: 'secondary' }
  }
}

/**
 * The latest commands run in VMs: an admin sees the organisation's, a
 * member their own VM's (the endpoint decides which).
 */
export function CommandsSection({ commands }: Pick<DashboardData, 'commands'>) {
  const list = commands.data ?? []
  const active = list.filter((c) => c.state === 'queued' || c.state === 'handed_out' || c.state === 'running')
  return (
    <SectionCard
      title="Commands in VMs"
      to="/vm-commands"
      linkLabel="The whole command log"
      loading={commands.isLoading}
      failed={commands.isError}
    >
      <Facts
        facts={[
          { label: 'In progress', value: active.length.toLocaleString() },
          {
            label: `Failed, of the last ${list.length}`,
            value: list.filter(commandFailed).length.toLocaleString(),
            tone: list.some(commandFailed) ? 'bad' : undefined,
          },
        ]}
      />
      <Rows empty="No command has run in a VM yet.">
        {list.slice(0, RECENT_LIMIT).map((c) => {
          const state = commandState(c)
          return (
            <li key={c.command_id} className="dashboard-row">
              <Text className="dashboard-row-main dashboard-code" title={c.command}>
                {c.command}
              </Text>
              <Text size="small" className="dashboard-muted dashboard-row-meta">
                <Label variant={state.variant}>{state.word}</Label> <RelativeTime datetime={c.created_at} />
              </Text>
            </li>
          )
        })}
      </Rows>
    </SectionCard>
  )
}
