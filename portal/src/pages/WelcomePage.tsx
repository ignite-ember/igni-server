import { useNavigate } from 'react-router-dom'
import { Button, Heading, Label, PageHeader, Stack, Text } from '@primer/react'
import { CheckIcon, RocketIcon, TerminalIcon } from '@primer/octicons-react'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import './WelcomePage.css'

function CommandBlock({ children }: { children: string }) {
  return (
    <pre className="welcome-cmd">
      <span className="welcome-cmd-prompt">$</span>
      <code>{children}</code>
    </pre>
  )
}

export function WelcomePage() {
  useDocumentTitle('Welcome')
  const navigate = useNavigate()

  return (
    <Stack gap="spacious" className="welcome-page">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <RocketIcon size={24} />
          </PageHeader.LeadingVisual>
          <PageHeader.Title as="h1">Welcome to igni</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="medium" className="welcome-subtitle">
            Install the CLI and you're ready to go.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      <Stack gap="normal" className="welcome-step">
        <Heading as="h2" variant="medium">
          <TerminalIcon /> Install the CLI
        </Heading>
        <Text className="welcome-step-desc">
          The igni CLI runs locally and connects to your account. Pick whichever installer fits your machine — both produce the same <code>igni</code> command.
        </Text>
        <Stack gap="condensed">
          <Label variant="secondary">macOS / Linux (Homebrew)</Label>
          <CommandBlock>brew install ignite-ember/tap/ignite-ember</CommandBlock>
          <Label variant="secondary">Cross-platform (Python ≥ 3.12)</Label>
          <CommandBlock>pip install ignite-ember</CommandBlock>
        </Stack>
        <Stack gap="condensed">
          {/* `/login` is a slash command *inside* the session, not a
              shell argument. This block said `ignite-ember /login`,
              which exits with "Got unexpected extra argument (/login)"
              — the first instruction a new account is given, and it
              fails. Two lines, because it is two steps. */}
          <Label variant="secondary">Then start it and authenticate this machine</Label>
          <CommandBlock>igni</CommandBlock>
          <Text size="small" className="welcome-muted">
            Then type <code>/login</code> at the prompt. A browser tab will open — confirm and
            you&rsquo;re paired.
          </Text>
        </Stack>
      </Stack>

      <Stack
        direction="horizontal"
        gap="condensed"
        align="center"
        className="welcome-footer"
        wrap="wrap"
      >
        <Stack direction="horizontal" gap="condensed" align="center" className="welcome-muted">
          <CheckIcon size={16} />
          <Text size="small">Your account and a personal organisation are ready.</Text>
        </Stack>
        <Button onClick={() => navigate('/dashboard')} variant="primary">
          Continue to dashboard
        </Button>
      </Stack>
    </Stack>
  )
}

