import { ActionList, ActionMenu, IconButton } from '@primer/react'
import {
  ArrowSwitchIcon,
  CheckCircleIcon,
  KebabHorizontalIcon,
  NoEntryIcon,
  SignOutIcon,
  TrashIcon,
} from '@primer/octicons-react'

import type { TeamMember, TeamRole } from '../../types'
import { ROLE_OPTIONS } from './roles'

export interface MemberActionHandlers {
  onChangeRole: (member: TeamMember, role: TeamRole) => void
  onToggleStatus: (member: TeamMember) => void
  onRevokeSessions: (member: TeamMember) => void
  onRemove: (member: TeamMember) => void
}

/**
 * The row menu an admin opens on a member.
 *
 * Role, status and remove are all refused on self at the backend, and a
 * table row is the wrong place to sign yourself out of, so the menu on
 * the signed-in admin's own row is empty.
 */
export function MemberActions({
  member,
  isSelf,
  handlers,
}: {
  member: TeamMember
  isSelf: boolean
  handlers: MemberActionHandlers
}) {
  return (
    <ActionMenu>
      <ActionMenu.Anchor>
        <IconButton
          icon={KebabHorizontalIcon}
          variant="invisible"
          size="small"
          aria-label={`Actions for ${member.full_name ?? member.email}`}
        />
      </ActionMenu.Anchor>
      <ActionMenu.Overlay>
        <ActionList>{!isSelf && <MemberActionItems member={member} handlers={handlers} />}</ActionList>
      </ActionMenu.Overlay>
    </ActionMenu>
  )
}

function MemberActionItems({ member, handlers }: { member: TeamMember; handlers: MemberActionHandlers }) {
  const isActive = member.status === 'active'
  // Only active members can be deactivated, and only deactivated members
  // can be reactivated. `invited` members are still pending acceptance —
  // the claim flow promotes them on signup.
  const canToggleStatus = isActive || member.status === 'deactivated'

  return (
    <>
      <ActionList.Group title="Change role">
        {ROLE_OPTIONS.map((option) => (
          <ActionList.Item
            key={option.role}
            onSelect={() => handlers.onChangeRole(member, option.role)}
            disabled={member.role === option.role}
          >
            <ActionList.LeadingVisual>
              <option.icon size={16} />
            </ActionList.LeadingVisual>
            {option.label}
            {member.role === option.role && (
              <ActionList.TrailingVisual>
                <CheckCircleIcon size={16} />
              </ActionList.TrailingVisual>
            )}
          </ActionList.Item>
        ))}
      </ActionList.Group>
      {canToggleStatus && (
        <>
          <ActionList.Divider />
          <ActionList.Item onSelect={() => handlers.onToggleStatus(member)}>
            <ActionList.LeadingVisual>
              {isActive ? <NoEntryIcon size={16} /> : <ArrowSwitchIcon size={16} />}
            </ActionList.LeadingVisual>
            {isActive ? 'Deactivate' : 'Reactivate'}
          </ActionList.Item>
        </>
      )}
      <ActionList.Divider />
      <ActionList.Item onSelect={() => handlers.onRevokeSessions(member)}>
        <ActionList.LeadingVisual>
          <SignOutIcon size={16} />
        </ActionList.LeadingVisual>
        Sign out everywhere
        <ActionList.Description variant="block">
          Withdraws every token they hold. They can sign in again.
        </ActionList.Description>
      </ActionList.Item>
      <ActionList.Divider />
      <ActionList.Item variant="danger" onSelect={() => handlers.onRemove(member)}>
        <ActionList.LeadingVisual>
          <TrashIcon size={16} />
        </ActionList.LeadingVisual>
        Remove from team
      </ActionList.Item>
    </>
  )
}
