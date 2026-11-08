import { useEffect, useRef, useState, useCallback } from 'react'
import { ActionList, ActionMenu, Dialog, FormControl, NavList, Stack, Text, TextInput, VisuallyHidden } from '@primer/react'
import { SkeletonAvatar } from '@primer/react/experimental'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router-dom'
import {
  HomeIcon,
  PeopleIcon,
  GearIcon,
  SignOutIcon,
  OrganizationIcon,
  PlusIcon,
  PencilIcon,
  TrashIcon,
  ChevronDownIcon,
  CpuIcon,
  ShieldLockIcon,
  ShieldCheckIcon,
  ServerIcon,
  AppsIcon,
  ContainerIcon,
  TerminalIcon,
  KeyAsteriskIcon,
  KeyIcon,
  LawIcon,
} from '@primer/octicons-react'

import { useAuth } from '../../auth/useAuth'
import { useOrg } from '../../auth/useOrg'
import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { UserMe } from '../../types'
import { Licensee } from './Licensee'
import { resourceItems } from './sidebarResources'
import './Sidebar.css'

const mainItems = [
  { label: 'Dashboard', path: 'dashboard', icon: HomeIcon },
]

const orgItems = [
  { label: 'Team', path: 'team', icon: PeopleIcon },
  { label: 'Groups', path: 'groups', icon: PeopleIcon },
  { label: 'Inference', path: 'inference', icon: CpuIcon },
  // The cloud: apps are the organisation's other producers, VM profiles
  // what each producer's microVM gets. Both are org-admin only.
  { label: 'Apps', path: 'apps', icon: AppsIcon, adminOnly: true },
  { label: 'VM profiles', path: 'vm-profiles', icon: ContainerIcon, adminOnly: true },
  // Anybody may run a command in their own VM and read their own log.
  { label: 'Commands in VMs', path: 'vm-commands', icon: TerminalIcon },
  // Your own VM's credential, or — for an admin — everyone's.
  { label: 'VM credentials', path: 'vm-credentials', icon: KeyIcon },
  // Your access, handed to a program; an admin sees everybody's.
  { label: 'API keys', path: 'api-keys', icon: KeyAsteriskIcon },
  // Former members' and discarded apps' VMs on their way to erasure.
  { label: 'Erasures', path: 'erasures', icon: TrashIcon, adminOnly: true },
  // Every endpoint behind this page is `require_admin`, so the entry is
  // hidden for a member rather than shown and then refused — the flaw
  // F103 found on the login page, where three provider buttons appeared
  // whether or not the server would accept them.
  { label: 'Compliance', path: 'compliance', icon: ShieldLockIcon, adminOnly: true },
  { label: 'Settings', path: 'settings', icon: GearIcon },
]

function UserInitials({ name, size = 20 }: { name: string; size?: number }) {
  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w.charAt(0).toUpperCase())
    .join('')

  return (
    <span
      className="sidebar-avatar"
      style={{ width: size, height: size, fontSize: size * 0.45 }}
    >
      {initials}
    </span>
  )
}

function OrgInitial({ name }: { name: string }) {
  return (
    <span className="sidebar-org-icon">
      {name.charAt(0).toUpperCase()}
    </span>
  )
}

export function CreateOrgDialog({
  onClose,
  returnFocusRef,
}: {
  onClose: () => void
  returnFocusRef: React.RefObject<HTMLButtonElement | null>
}) {
  const [name, setName] = useState('')
  const [domains, setDomains] = useState('')
  const queryClient = useQueryClient()
  const { switchOrg } = useOrg()
  // Commas or spaces: people paste "acme.com, acme.co.uk" as often as one.
  const domainList = domains.split(/[\s,]+/).map((d) => d.trim()).filter(Boolean)

  const createMutation = useMutation({
    mutationFn: (body: { display_name: string; domains: string[] }) =>
      client.post<{ id: string; slug: string; display_name: string }>('/portal/organizations', body).then((r) => r.data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['me'] })
      queryClient.invalidateQueries({ queryKey: ['deployment-organizations'] })
      switchOrg(data.id)
      onClose()
      setName('')
      setDomains('')
    },
  })

  const handleCreate = useCallback(() => {
    if (!name.trim() || domainList.length === 0) return
    createMutation.mutate({ display_name: name.trim(), domains: domainList })
  }, [name, domainList, createMutation])

  return (
    <Dialog
      title="Create organization"
      onClose={onClose}
      returnFocusRef={returnFocusRef as React.RefObject<HTMLElement>}
      footerButtons={[
        { content: 'Cancel', onClick: onClose },
        {
          content: createMutation.isPending ? 'Creating...' : 'Create',
          buttonType: 'primary',
          onClick: handleCreate,
          disabled: !name.trim() || domainList.length === 0 || createMutation.isPending,
        },
      ]}
    >
      <Stack gap="normal">
        <FormControl>
          <FormControl.Label>Organization name</FormControl.Label>
          <TextInput
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Acme Corp"
            block
            autoFocus
          />
        </FormControl>
        <FormControl>
          <FormControl.Label>Email domains</FormControl.Label>
          <FormControl.Caption>
            People with an address on these domains join this organization when they sign up. No two
            organizations can share a domain.
          </FormControl.Caption>
          <TextInput
            value={domains}
            onChange={(e) => setDomains(e.target.value)}
            placeholder="acme.com"
            block
          />
        </FormControl>
        {createMutation.isError && (
          <Text size="small" className="color-fg-danger">
            {refusalOf(createMutation.error, 'The organization was not created.')}
          </Text>
        )}
      </Stack>
    </Dialog>
  )
}

function RenameOrgDialog({
  orgId,
  currentName,
  onClose,
  returnFocusRef,
}: {
  orgId: string
  currentName: string
  onClose: () => void
  returnFocusRef: React.RefObject<HTMLButtonElement | null>
}) {
  const [name, setName] = useState(currentName)
  const queryClient = useQueryClient()

  const renameMutation = useMutation({
    mutationFn: (displayName: string) =>
      client.patch(`/portal/organizations/${orgId}`, { display_name: displayName }).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] })
      onClose()
    },
  })

  const dirty = name.trim() !== currentName
  const handleRename = useCallback(() => {
    if (!name.trim() || !dirty) return
    renameMutation.mutate(name.trim())
  }, [name, dirty, renameMutation])

  return (
    <Dialog
      title="Rename organization"
      onClose={onClose}
      returnFocusRef={returnFocusRef as React.RefObject<HTMLElement>}
      footerButtons={[
        { content: 'Cancel', onClick: onClose },
        {
          content: renameMutation.isPending ? 'Saving...' : 'Save',
          buttonType: 'primary',
          onClick: handleRename,
          disabled: !dirty || !name.trim() || renameMutation.isPending,
        },
      ]}
    >
      <FormControl>
        <FormControl.Label>Organization name</FormControl.Label>
        <TextInput
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Acme Corp"
          block
          autoFocus
        />
      </FormControl>
    </Dialog>
  )
}

function DeleteOrgDialog({
  orgId,
  orgName,
  onClose,
  returnFocusRef,
}: {
  orgId: string
  orgName: string
  onClose: () => void
  returnFocusRef: React.RefObject<HTMLButtonElement | null>
}) {
  const [confirm, setConfirm] = useState('')
  const queryClient = useQueryClient()
  const { organizations, switchOrg } = useOrg()

  const deleteMutation = useMutation({
    mutationFn: () => client.delete(`/portal/organizations/${orgId}`).then((r) => r.data),
    onSuccess: () => {
      const remaining = organizations.filter((o) => o.id !== orgId)
      if (remaining.length > 0) {
        switchOrg(remaining[0].id)
      }
      queryClient.invalidateQueries({ queryKey: ['me'] })
      onClose()
    },
  })

  const canDelete = confirm === orgName

  return (
    <Dialog
      title="Delete organization"
      onClose={onClose}
      returnFocusRef={returnFocusRef as React.RefObject<HTMLElement>}
      footerButtons={[
        { content: 'Cancel', onClick: onClose },
        {
          content: deleteMutation.isPending ? 'Deleting...' : 'Delete',
          buttonType: 'danger',
          onClick: () => deleteMutation.mutate(),
          disabled: !canDelete || deleteMutation.isPending,
        },
      ]}
    >
      <Stack gap="normal">
        <Text>
          This will permanently delete <strong>{orgName}</strong> and remove all members.
          This action cannot be undone.
        </Text>
        <FormControl>
          <FormControl.Label>
            Type <strong>{orgName}</strong> to confirm
          </FormControl.Label>
          <TextInput
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={orgName}
            block
            autoFocus
          />
        </FormControl>
      </Stack>
    </Dialog>
  )
}

function DeleteAccountDialog({
  email,
  onClose,
  returnFocusRef,
}: {
  email: string
  onClose: () => void
  returnFocusRef: React.RefObject<HTMLButtonElement | null>
}) {
  const [confirm, setConfirm] = useState('')
  const { logout } = useAuth()

  const deleteMutation = useMutation({
    mutationFn: () => client.delete('/portal/me').then((r) => r.data),
    onSuccess: () => {
      onClose()
      logout()
    },
  })

  const canDelete = confirm === email

  return (
    <Dialog
      title="Delete account"
      onClose={onClose}
      returnFocusRef={returnFocusRef as React.RefObject<HTMLElement>}
      footerButtons={[
        { content: 'Cancel', onClick: onClose },
        {
          content: deleteMutation.isPending ? 'Deleting...' : 'Delete account',
          buttonType: 'danger',
          onClick: () => deleteMutation.mutate(),
          disabled: !canDelete || deleteMutation.isPending,
        },
      ]}
    >
      <Stack gap="normal">
        <Text>
          This will permanently delete your account, remove you from all organizations,
          and erase your data. This action cannot be undone.
        </Text>
        <FormControl>
          <FormControl.Label>
            Type <strong>{email}</strong> to confirm
          </FormControl.Label>
          <TextInput
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={email}
            block
            autoFocus
          />
        </FormControl>
      </Stack>
    </Dialog>
  )
}

export function Sidebar() {
  const location = useLocation()
  const { logout } = useAuth()
  const { activeOrg, organizations, switchOrg, setOrganizations } = useOrg()
  const [showCreateOrg, setShowCreateOrg] = useState(false)
  const [renameOrg, setRenameOrg] = useState<{ id: string; name: string } | null>(null)
  const [deleteOrg, setDeleteOrg] = useState<{ id: string; name: string } | null>(null)
  const [showDeleteAccount, setShowDeleteAccount] = useState(false)
  const createOrgRef = useRef<HTMLButtonElement>(null)
  const userBtnRef = useRef<HTMLButtonElement>(null)

  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })

  // Sync orgs from /me response into OrgProvider.
  // Only pass org_id on initial load (no stored selection yet) so that
  // subsequent refetches (e.g. after rename) don't override the user's choice.
  const initialOrgApplied = useRef(false)
  useEffect(() => {
    if (meQuery.data?.organizations) {
      const hint = initialOrgApplied.current ? undefined : (meQuery.data.org_id ?? undefined)
      setOrganizations(meQuery.data.organizations, hint)
      initialOrgApplied.current = true
    }
  }, [meQuery.data, setOrganizations])

  const displayName = meQuery.data?.full_name || meQuery.data?.email || 'User'
  const orgRole = activeOrg?.role ?? 'member'

  function isCurrent(path: string) {
    const href = `/${path}`
    return location.pathname === href || location.pathname.startsWith(`${href}/`)
  }

  function toHref(path: string) {
    return `/${path}`
  }

  /**
   * Props that make a nav entry a real link.
   *
   * These items were `<NavList.Item onClick={() => navigate(...)}>`, so
   * Primer rendered an `<a>` with **no `href`**. Left-click worked, and
   * nothing else did: cmd-click did not open a new tab — it navigated
   * the current one, losing the page you were on — and middle-click,
   * "open in new tab" and "copy link address" were all dead. Hovering
   * showed no destination in the status bar.
   *
   * `as={Link}` hands the click to React Router, which already knows to
   * ignore modified clicks and let the browser do what the user asked.
   * The same idiom the docs breadcrumb uses.
   */
  function linkTo(path: string) {
    return { as: Link, to: toHref(path) } as const
  }

  return (
    <div className="sidebar-root">
      {/* Combined org + user identity card */}
      <div className="sidebar-identity">
        {/* Org section */}
        <ActionMenu>
          <ActionMenu.Anchor>
            {/* Named by what it shows, then what it does: an aria-label would
                replace the visible org name, and a voice-control user who
                says the name they see would miss it (WCAG 2.5.3). */}
            <button ref={createOrgRef} className="sidebar-org-section">
              {activeOrg ? <OrgInitial name={activeOrg.display_name} /> : <OrganizationIcon size={16} />}
              <span className="sidebar-org-text">
                <span className="sidebar-org-name">
                  {activeOrg?.display_name ?? 'Select org'}
                </span>
                <span className="sidebar-org-role">{orgRole}</span>
              </span>
              <VisuallyHidden>Switch organization</VisuallyHidden>
              <ChevronDownIcon size={16} />
            </button>
          </ActionMenu.Anchor>
          <ActionMenu.Overlay>
            <ActionList>
              <ActionList.Group selectionVariant="single">
                {organizations.map((org) => (
                  <ActionList.Item
                    key={org.id}
                    selected={org.id === activeOrg?.id}
                    onSelect={() => switchOrg(org.id)}
                  >
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
                      onSelect={() => setRenameOrg({ id: activeOrg.id, name: activeOrg.display_name })}
                    >
                      <ActionList.LeadingVisual><PencilIcon /></ActionList.LeadingVisual>
                      Rename
                    </ActionList.Item>
                    <ActionList.Item
                      variant="danger"
                      onSelect={() => setDeleteOrg({ id: activeOrg.id, name: activeOrg.display_name })}
                    >
                      <ActionList.LeadingVisual><TrashIcon /></ActionList.LeadingVisual>
                      Delete
                    </ActionList.Item>
                  </ActionList.Group>
                </>
              )}
              {/* Organisations are created by super admins only: an
                  install holds one or two, and people join them by
                  invitation. */}
              {meQuery.data?.is_super_admin === true && (
                <>
                  <ActionList.Divider />
                  <ActionList.Item onSelect={() => setShowCreateOrg(true)}>
                    <ActionList.LeadingVisual><PlusIcon /></ActionList.LeadingVisual>
                    Create organization
                  </ActionList.Item>
                </>
              )}
            </ActionList>
          </ActionMenu.Overlay>
        </ActionMenu>

        <hr className="sidebar-identity-divider" />

        {/* User section */}
        <ActionMenu>
          <ActionMenu.Anchor>
            <button ref={userBtnRef} className="sidebar-user-section">
              {meQuery.isLoading ? (
                <SkeletonAvatar size={20} />
              ) : (
                <UserInitials name={displayName} size={20} />
              )}
              <span className="sidebar-user-name">{meQuery.isLoading ? '' : displayName}</span>
              <VisuallyHidden>User menu</VisuallyHidden>
            </button>
          </ActionMenu.Anchor>
          <ActionMenu.Overlay>
            <ActionList>
              <ActionList.Item variant="danger" onSelect={() => setShowDeleteAccount(true)}>
                <ActionList.LeadingVisual><TrashIcon /></ActionList.LeadingVisual>
                Delete account
              </ActionList.Item>
              <ActionList.Divider />
              <ActionList.Item onSelect={logout}>
                <ActionList.LeadingVisual><SignOutIcon /></ActionList.LeadingVisual>
                Sign out
              </ActionList.Item>
            </ActionList>
          </ActionMenu.Overlay>
        </ActionMenu>
      </div>

      <NavList aria-label="Main navigation">
        <NavList.Group title="Overview">
          {mainItems.map((item) => (
            <NavList.Item
              key={item.path}
              {...linkTo(item.path)}
              aria-current={isCurrent(item.path) ? 'page' : undefined}
            >
              <NavList.LeadingVisual>
                <item.icon />
              </NavList.LeadingVisual>
              {item.label}
            </NavList.Item>
          ))}
        </NavList.Group>

        <NavList.Group title="Organization">
          {orgItems
            .filter((item) => !('adminOnly' in item && item.adminOnly) || activeOrg?.role === 'admin')
            .map((item) => (
            <NavList.Item
              key={item.path}
              {...linkTo(item.path)}
              aria-current={isCurrent(item.path) ? 'page' : undefined}
            >
              <NavList.LeadingVisual>
                <item.icon />
              </NavList.LeadingVisual>
              {item.label}
            </NavList.Item>
          ))}
        </NavList.Group>

        {meQuery.data?.is_super_admin === true && (
          <NavList.Group title="Deployment">
            <NavList.Item {...linkTo('organisations')} aria-current={isCurrent('organisations') ? 'page' : undefined}>
              <NavList.LeadingVisual>
                <OrganizationIcon />
              </NavList.LeadingVisual>
              Organisations
            </NavList.Item>
            <NavList.Item {...linkTo('super-admins')} aria-current={isCurrent('super-admins') ? 'page' : undefined}>
              <NavList.LeadingVisual>
                <ShieldCheckIcon />
              </NavList.LeadingVisual>
              Super admins
            </NavList.Item>
            <NavList.Item {...linkTo('services')} aria-current={isCurrent('services') ? 'page' : undefined}>
              <NavList.LeadingVisual>
                <ServerIcon />
              </NavList.LeadingVisual>
              Services
            </NavList.Item>
            <NavList.Item {...linkTo('cloud-limits')} aria-current={isCurrent('cloud-limits') ? 'page' : undefined}>
              <NavList.LeadingVisual>
                <ContainerIcon />
              </NavList.LeadingVisual>
              Cloud limits
            </NavList.Item>
            <NavList.Item {...linkTo('licence')} aria-current={isCurrent('licence') ? 'page' : undefined}>
              <NavList.LeadingVisual>
                <LawIcon />
              </NavList.LeadingVisual>
              Licence
            </NavList.Item>
          </NavList.Group>
        )}

        <NavList.Group title="Resources">
          {/* Published on the public site, not routed here — a plain
          link out rather than a router `Link`. */}
          {resourceItems.map((item) => (
            <NavList.Item key={item.href} href={item.href}>
              <NavList.LeadingVisual>
                <item.icon />
              </NavList.LeadingVisual>
              {item.label}
            </NavList.Item>
          ))}
        </NavList.Group>
      </NavList>

      <Licensee />

      {showCreateOrg && (
        <CreateOrgDialog
          onClose={() => setShowCreateOrg(false)}
          returnFocusRef={createOrgRef}
        />
      )}

      {renameOrg && (
        <RenameOrgDialog
          orgId={renameOrg.id}
          currentName={renameOrg.name}
          onClose={() => setRenameOrg(null)}
          returnFocusRef={createOrgRef}
        />
      )}

      {deleteOrg && (
        <DeleteOrgDialog
          orgId={deleteOrg.id}
          orgName={deleteOrg.name}
          onClose={() => setDeleteOrg(null)}
          returnFocusRef={createOrgRef}
        />
      )}

      {showDeleteAccount && meQuery.data?.email && (
        <DeleteAccountDialog
          email={meQuery.data.email}
          onClose={() => setShowDeleteAccount(false)}
          returnFocusRef={userBtnRef}
        />
      )}
    </div>
  )
}
