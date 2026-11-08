import { useRef, useState } from 'react'
import { ActionList, ActionMenu, VisuallyHidden } from '@primer/react'
import { SkeletonAvatar } from '@primer/react/experimental'
import { SignOutIcon, TrashIcon } from '@primer/octicons-react'

import { useAuth } from '../../auth/useAuth'
import type { UserMe } from '../../types'
import { DeleteAccountDialog } from '../account/DeleteAccountDialog'

function UserInitials({ name, size = 20 }: { name: string; size?: number }) {
  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w.charAt(0).toUpperCase())
    .join('')

  return (
    <span className="sidebar-avatar" style={{ width: size, height: size, fontSize: size * 0.45 }}>
      {initials}
    </span>
  )
}

/** The signed-in account: who it is, signing out, and deleting it. */
export function UserMenu({ me, isLoading }: { me: UserMe | undefined; isLoading: boolean }) {
  const { logout } = useAuth()
  const [deleting, setDeleting] = useState(false)
  const anchorRef = useRef<HTMLButtonElement>(null)
  const displayName = me?.full_name || me?.email || 'User'

  return (
    <>
      <ActionMenu>
        <ActionMenu.Anchor>
          <button ref={anchorRef} className="sidebar-user-section">
            {isLoading ? <SkeletonAvatar size={20} /> : <UserInitials name={displayName} size={20} />}
            <span className="sidebar-user-name">{isLoading ? '' : displayName}</span>
            <VisuallyHidden>User menu</VisuallyHidden>
          </button>
        </ActionMenu.Anchor>
        <ActionMenu.Overlay>
          <ActionList>
            <ActionList.Item variant="danger" onSelect={() => setDeleting(true)}>
              <ActionList.LeadingVisual>
                <TrashIcon />
              </ActionList.LeadingVisual>
              Delete account
            </ActionList.Item>
            <ActionList.Divider />
            <ActionList.Item onSelect={logout}>
              <ActionList.LeadingVisual>
                <SignOutIcon />
              </ActionList.LeadingVisual>
              Sign out
            </ActionList.Item>
          </ActionList>
        </ActionMenu.Overlay>
      </ActionMenu>

      {deleting && me?.email && (
        <DeleteAccountDialog email={me.email} onClose={() => setDeleting(false)} returnFocusRef={anchorRef} />
      )}
    </>
  )
}
