import { useRef, useState } from 'react'
import { ActionList, ActionMenu, VisuallyHidden } from '@primer/react'
import { ChevronDownIcon, OrganizationIcon, PencilIcon, PlusIcon, TrashIcon } from '@primer/octicons-react'

import { useOrg } from '../../auth/useOrg'
import { CreateOrgDialog } from '../org/CreateOrgDialog'
import { DeleteOrgDialog } from '../org/DeleteOrgDialog'
import { RenameOrgDialog } from '../org/RenameOrgDialog'

type OrgDialog = { kind: 'create' } | { kind: 'rename' | 'delete'; id: string; name: string }

function OrgInitial({ name }: { name: string }) {
  return <span className="sidebar-org-icon">{name.charAt(0).toUpperCase()}</span>
}

/** The org switcher: pick the active organisation, and an admin's org actions. */
export function OrgMenu({ isSuperAdmin }: { isSuperAdmin: boolean }) {
  const { activeOrg, organizations, switchOrg } = useOrg()
  const [dialog, setDialog] = useState<OrgDialog | null>(null)
  const anchorRef = useRef<HTMLButtonElement>(null)
  const close = () => setDialog(null)

  return (
    <>
      <ActionMenu>
        <ActionMenu.Anchor>
          {/* Named by what it shows, then what it does: an aria-label would
              replace the visible org name, and a voice-control user who
              says the name they see would miss it (WCAG 2.5.3). */}
          <button ref={anchorRef} className="sidebar-org-section">
            {activeOrg ? <OrgInitial name={activeOrg.display_name} /> : <OrganizationIcon size={16} />}
            <span className="sidebar-org-text">
              <span className="sidebar-org-name">{activeOrg?.display_name ?? 'Select org'}</span>
              <span className="sidebar-org-role">{activeOrg?.role ?? 'member'}</span>
            </span>
            <VisuallyHidden>Switch organization</VisuallyHidden>
            <ChevronDownIcon size={16} />
          </button>
        </ActionMenu.Anchor>
        <ActionMenu.Overlay>
          <ActionList>
            <ActionList.Group selectionVariant="single">
              {organizations.map((org) => (
                <ActionList.Item key={org.id} selected={org.id === activeOrg?.id} onSelect={() => switchOrg(org.id)}>
                  {org.display_name}
                  <ActionList.Description>{org.slug}</ActionList.Description>
                </ActionList.Item>
              ))}
            </ActionList.Group>
            {activeOrg?.role === 'admin' && (
              <>
                <ActionList.Divider />
                <ActionList.Group title={activeOrg.display_name}>
                  <ActionList.Item
                    onSelect={() => setDialog({ kind: 'rename', id: activeOrg.id, name: activeOrg.display_name })}
                  >
                    <ActionList.LeadingVisual>
                      <PencilIcon />
                    </ActionList.LeadingVisual>
                    Rename
                  </ActionList.Item>
                  <ActionList.Item
                    variant="danger"
                    onSelect={() => setDialog({ kind: 'delete', id: activeOrg.id, name: activeOrg.display_name })}
                  >
                    <ActionList.LeadingVisual>
                      <TrashIcon />
                    </ActionList.LeadingVisual>
                    Delete
                  </ActionList.Item>
                </ActionList.Group>
              </>
            )}
            {/* Organisations are created by super admins only: an
                install holds one or two, and people join them by
                invitation. */}
            {isSuperAdmin && (
              <>
                <ActionList.Divider />
                <ActionList.Item onSelect={() => setDialog({ kind: 'create' })}>
                  <ActionList.LeadingVisual>
                    <PlusIcon />
                  </ActionList.LeadingVisual>
                  Create organization
                </ActionList.Item>
              </>
            )}
          </ActionList>
        </ActionMenu.Overlay>
      </ActionMenu>

      {dialog?.kind === 'create' && <CreateOrgDialog onClose={close} returnFocusRef={anchorRef} />}
      {dialog?.kind === 'rename' && (
        <RenameOrgDialog orgId={dialog.id} currentName={dialog.name} onClose={close} returnFocusRef={anchorRef} />
      )}
      {dialog?.kind === 'delete' && (
        <DeleteOrgDialog orgId={dialog.id} orgName={dialog.name} onClose={close} returnFocusRef={anchorRef} />
      )}
    </>
  )
}
