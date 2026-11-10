import { useState } from 'react'
import { Button, ConfirmationDialog, SelectPanel, Text } from '@primer/react'
import type { SelectPanelItemInput } from '@primer/react'
import { PersonAddIcon, TriangleDownIcon } from '@primer/octicons-react'

import type { TeamMember } from '../../types'

export function AddMemberMenu({
  members,
  memberUserIds,
  currentGroupOf,
  onMove,
  movePending,
}: {
  members: TeamMember[]
  memberUserIds: Set<string>
  /** Everybody not in this group → the one group they are in now. */
  currentGroupOf: Map<string, string>
  onMove: (userId: string) => void
  movePending: boolean
}) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const [confirmMove, setConfirmMove] = useState<{ id: string; name: string; fromGroup: string | undefined } | null>(
    null
  )

  const filtered = members.filter((m) => {
    const f = filter.toLowerCase()
    return (
      !f ||
      (m.full_name?.toLowerCase().includes(f) ?? false) ||
      (m.email?.toLowerCase().includes(f) ?? false)
    )
  })

  function handleSelect(userId: string) {
    const user = members.find((m) => m.id === userId)
    if (!user) return
    if (memberUserIds.has(userId)) return
    setConfirmMove({ id: userId, name: user.full_name ?? user.email ?? userId, fromGroup: currentGroupOf.get(userId) })
  }

  function doMove(userId: string) {
    onMove(userId)
    setOpen(false)
    setFilter('')
  }

  return (
    <>
      <SelectPanel
        // The anchor says what it does — its own children replace the
        // placeholder SelectPanel hands it: nothing stays selected after a move.
        renderAnchor={(anchorProps) => (
          <Button {...anchorProps} trailingAction={TriangleDownIcon}>
            <PersonAddIcon /> Move someone here
          </Button>
        )}
        title="Move someone here"
        subtitle="Everybody is in exactly one group. Moving someone here takes them out of the group they are in now."
        placeholder="Select a member"
        open={open}
        onOpenChange={(o) => { setOpen(o); if (!o) setFilter('') }}
        items={filtered.map((m) => ({
          text: m.full_name ?? m.email ?? m.id,
          description: [
            m.full_name ? m.email : null,
            memberUserIds.has(m.id)
              ? 'Already here'
              : currentGroupOf.get(m.id)
                ? `Currently in ${currentGroupOf.get(m.id)}`
                : null,
          ].filter(Boolean).join(' · ') || undefined,
          id: m.id,
          disabled: memberUserIds.has(m.id) || !m.full_name && !m.email,
        }))}
        filterValue={filter}
        onFilterChange={setFilter}
        placeholderText="Filter by name or email"
        inputLabel="Search members"
        message={filtered.length === 0 ? { title: 'No matches', body: 'Try a different search.', variant: 'empty' } : undefined}
        selected={undefined}
        onSelectedChange={(item: SelectPanelItemInput | undefined) => {
          if (!item || item.id === undefined) return
          handleSelect(String(item.id))
        }}
      />

      {confirmMove && (
        <ConfirmationDialog
          title={`Move "${confirmMove.name}" to this group?`}
          confirmButtonContent={movePending ? 'Moving…' : 'Move member'}
          cancelButtonContent="Cancel"
          confirmButtonType="primary"
          onClose={(gesture) => {
            if (gesture === 'confirm') {
              doMove(confirmMove.id)
            }
            setConfirmMove(null)
          }}
        >
          <Text size="medium">
            {confirmMove.fromGroup ? (
              <>
                This member is in <Text weight="semibold">{confirmMove.fromGroup}</Text>. Moving them here takes them
                out of it.
              </>
            ) : (
              'Moving them here takes them out of the group they are in now.'
            )}
          </Text>
        </ConfirmationDialog>
      )}
    </>
  )
}
