import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import {
  Banner,
  Button,
  ConfirmationDialog,
  FormControl,
  Heading,
  PageHeader,
  SkeletonBox,
  Stack,
  Text,
  TextInput,
} from '@primer/react'
import { SkeletonText } from '@primer/react/experimental'
import { CheckIcon } from '@primer/octicons-react'
import client from '../api/client'
import { useAuth } from '../auth/useAuth'
import type { UserMe } from '../types'
import './cards.css'
import './SettingsPage.css'

// ─── Profile ────────────────────────────────────────────────────────

export function ProfileForm({ user }: { user: UserMe }) {
  const queryClient = useQueryClient()
  const [fullName, setFullName] = useState(user.full_name ?? '')
  const [saved, setSaved] = useState(false)

  const updateMutation = useMutation({
    mutationFn: (data: { full_name: string }) =>
      client.patch<UserMe>('/portal/me', data).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] })
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    },
  })

  const dirty = fullName !== (user.full_name ?? '')

  const handleSave = () => {
    updateMutation.mutate({ full_name: fullName })
  }

  return (
    <Stack className="settings-card" padding="normal" gap="normal">
      <Stack gap="normal" className="settings-form">
        <FormControl>
          <FormControl.Label>Full name</FormControl.Label>
          <TextInput
            value={fullName}
            onChange={(e) => { setFullName(e.target.value); setSaved(false) }}
            placeholder="Your name"
            block
          />
        </FormControl>

        {/* `readOnly`, not `disabled`. Primer dims a disabled
            FormControl as a whole, and that dimming reached the caption
            — the one sentence on this page a person actually has to
            read — taking it to 3.24:1 against the panel. axe exempts a
            disabled *control* from contrast, not the guidance beside
            it. The field is still not editable. F137. */}
        <FormControl>
          <FormControl.Label>Email</FormControl.Label>
          <TextInput
            type="email"
            value={user.email ?? ''}
            block
            readOnly
            aria-readonly="true"
          />
          <FormControl.Caption>
            Email changes are disabled. The address is fixed to the account that
            signed up; an administrator of this deployment can change it directly.
          </FormControl.Caption>
        </FormControl>

        <Stack direction="horizontal" gap="condensed" align="center">
          <Button
            variant="primary"
            onClick={handleSave}
            disabled={!dirty || updateMutation.isPending}
            loading={updateMutation.isPending}
          >
            Save changes
          </Button>
          {saved && (
            <Stack direction="horizontal" align="center" gap="condensed" className="settings-saved">
              <CheckIcon size={16} />
              <Text size="small">Saved</Text>
            </Stack>
          )}
        </Stack>

        {updateMutation.isError && (
          <Banner variant="critical" title="Failed to update profile. Please try again." />
        )}
      </Stack>
    </Stack>
  )
}

// ─── Sessions ───────────────────────────────────────────────────────

/**
 * DEC-10/B5. "I have lost my laptop" without needing an administrator.
 *
 * The button withdraws every token this person holds by moving a
 * watermark on their account, so it needs no session list and there is
 * nothing here that could be out of date — which is also why this panel
 * shows no device list. It could not show one honestly: the design keeps
 * no per-session state, and a fabricated list of "devices" in a security
 * panel would be worse than none.
 *
 * It signs the caller out too, deliberately, so a 200 is followed by
 * `logout()` — clearing the stored token and returning to sign-in. Not
 * doing that would leave the portal holding a credential the server has
 * just started refusing, which reads as every page breaking at once.
 */
export function SessionsSection() {
  const { logout } = useAuth()
  const [confirming, setConfirming] = useState(false)

  const revokeMutation = useMutation({
    mutationFn: () => client.post('/portal/me/revoke-sessions'),
    onSuccess: () => logout(),
  })

  return (
    <Stack className="settings-card" padding="normal" gap="normal">
      <Stack gap="condensed">
        <Heading as="h2" variant="small">Sessions</Heading>
        <Text size="small" className="settings-hint">
          Signing out everywhere withdraws every token issued to you — this browser, the CLI and any
          editor you have paired — and takes effect on the next request each of them makes. Use it if
          a device has been lost or a token may have been copied. You can sign in again immediately.
        </Text>
      </Stack>

      <Stack direction="horizontal" gap="condensed" align="center">
        <Button onClick={() => setConfirming(true)} loading={revokeMutation.isPending}>
          Sign out everywhere
        </Button>
      </Stack>

      {revokeMutation.isError && (
        <Banner variant="critical" title="Could not withdraw your sessions. Please try again." />
      )}

      {confirming ? (
        <ConfirmationDialog
          title="Sign out of every device?"
          confirmButtonContent="Sign out everywhere"
          cancelButtonContent="Cancel"
          onClose={(gesture) => {
            setConfirming(false)
            if (gesture === 'confirm') revokeMutation.mutate()
          }}
        >
          <Text size="medium">
            You will be signed out here as well and will need to sign in again. Your account, your
            organisations and your linked accounts are untouched.
          </Text>
        </ConfirmationDialog>
      ) : null}
    </Stack>
  )
}

export function SettingsPage() {
  useDocumentTitle('Settings')
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
  })

  if (meQuery.isLoading) {
    return (
      <Stack gap="spacious">
        <PageHeader>
          <PageHeader.TitleArea>
            <PageHeader.Title as="h1">Settings</PageHeader.Title>
          </PageHeader.TitleArea>
        </PageHeader>
        <Stack className="settings-card" padding="normal" gap="normal">
          <Stack gap="normal" className="settings-form">
            <Stack gap="condensed">
              <SkeletonText size="bodySmall" maxWidth={80} />
              <SkeletonBox height={32} />
            </Stack>
            <Stack gap="condensed">
              <SkeletonText size="bodySmall" maxWidth={50} />
              <SkeletonBox height={32} />
            </Stack>
            <SkeletonBox height={32} width={120} />
          </Stack>
        </Stack>
      </Stack>
    )
  }

  if (!meQuery.data) {
    return <Banner variant="critical" title="Failed to load profile" />
  }

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Settings</PageHeader.Title>
        </PageHeader.TitleArea>
      </PageHeader>

      <ProfileForm user={meQuery.data} />
      <SessionsSection />
    </Stack>
  )
}
