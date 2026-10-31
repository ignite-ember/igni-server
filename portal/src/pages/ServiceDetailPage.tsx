import { Fragment, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom'
import {
  Banner,
  Breadcrumbs,
  Button,
  Heading,
  Label,
  Link,
  PageHeader,
  RelativeTime,
  Spinner,
  Stack,
  Text,
} from '@primer/react'
import { Blankslate } from '@primer/react/experimental'
import { ArrowLeftIcon, PackageIcon, ServerIcon } from '@primer/octicons-react'
import client from '../api/client'
import { HeldUpdatePanel } from '../components/HeldUpdatePanel'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { useServiceDecision } from '../hooks/useServiceDecision'
import {
  describeHints,
  ExtensionManifest,
  ExtensionPluginContents,
  type ManifestPackaging,
  type ManifestPromptEdit,
} from '../lib/extensionManifest'
import { STATE_LABEL } from '../lib/registeredClients'
import type { ExtensionReview, RegisteredClientDetail, ServiceExtensionDetail, UserMe } from '../types'
import './cards.css'
import './ServicesPage.css'
import './ServiceDetailPage.css'

const PACKAGING_LABEL: Record<ManifestPackaging['kind'], string> = {
  python: 'A Python package igni installs from the wheels in the bundle and runs on each machine',
  sidecar: 'A container image igni runs on each machine',
  endpoint: 'A service somebody else runs, which igni connects to',
  dev: 'A command run from a source checkout — development only',
}

const WORKSPACE_LABEL = {
  none: "None — it cannot see people's projects",
  read: "Reads people's projects",
  write: "Reads and changes people's projects",
} as const

/** Host grants worth a word beyond their name. */
const HOST_GRANT_NOTE: Record<string, string> = {
  decide: "Asks igni's local decision model about text it sends. Runs on the machine; nothing leaves it.",
  links: "Opens https links in the person's browser.",
}

const PLATFORM_LABEL: Record<string, string> = {
  any: 'Every platform',
  'macos-arm64': 'macOS (Apple silicon)',
  'linux-x86_64': 'Linux x86_64',
  'windows-x86_64': 'Windows x86_64',
}

/** `158822080` → `151.5 MB`. */
function shownSize(bytes: number | null): string {
  if (bytes === null) return 'size not stated'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** A setting's default as written in the manifest, so a string and a boolean read differently. */
function shownDefault(value: unknown): string {
  return value === undefined ? 'none' : JSON.stringify(value)
}

/** A `section` with a heading other parts of the page and tests can name it by. */
function Section({ id, title, caption, children }: { id: string; title: string; caption?: ReactNode; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="settings-card service-section">
      <Stack gap="normal">
        <Stack gap="condensed">
          <Heading as="h2" variant="small" id={id}>
            {title}
          </Heading>
          {caption && (
            <Text size="small" className="settings-section-caption">
              {caption}
            </Text>
          )}
        </Stack>
        {children}
      </Stack>
    </section>
  )
}

/** One kind of thing inside a section: a small heading, then the list or "None". */
function Part({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return (
    <Stack gap="condensed">
      <Text as="h3" size="medium" weight="semibold" className="service-part-heading">
        {title}
      </Text>
      {count === 0 ? (
        <Text size="small" className="settings-section-caption">
          None
        </Text>
      ) : (
        <ul className="service-list" aria-label={title}>
          {children}
        </ul>
      )}
    </Stack>
  )
}

function Code({ children }: { children: ReactNode }) {
  return <code className="service-code">{children}</code>
}

function Facts({ label, rows }: { label: string; rows: [string, ReactNode][] }) {
  return (
    <dl className="service-facts" aria-label={label}>
      {rows.map(([term, value]) => (
        <Fragment key={term}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </Fragment>
      ))}
    </dl>
  )
}

function NotStated() {
  return <span className="settings-section-caption">Not stated</span>
}

function WhatItIs({ extension, manifest }: { extension: ServiceExtensionDetail; manifest: ExtensionManifest }) {
  const packaging = manifest.packaging
  return (
    <Section id="extension-what" title="What it is">
      <Facts
        label="What it is"
        rows={[
          ['Name', extension.name],
          ['ID', <Code>{extension.extension_id}</Code>],
          ['Version', extension.version],
          ['Vendor', manifest.vendor ?? <NotStated />],
          ['Description', extension.description || <NotStated />],
          ['Trust', manifest.trust ? `${manifest.trust}, as its author labels it` : <NotStated />],
          ['igni contract', manifest.contract ? <Code>{manifest.contract}</Code> : <NotStated />],
          [
            'How it runs',
            packaging ? (
              <Stack gap="none">
                <Text>{PACKAGING_LABEL[packaging.kind]}</Text>
                {packaging.details.map((detail) => (
                  <Text size="small" key={detail.label}>
                    {detail.label}: <Code>{detail.value}</Code>
                  </Text>
                ))}
              </Stack>
            ) : (
              <NotStated />
            ),
          ],
          ['SHA-256', <Code>{extension.sha256}</Code>],
          ['Published', <RelativeTime datetime={extension.published_at} />],
        ]}
      />
    </Section>
  )
}

function WhatItAdds({ manifest }: { manifest: ExtensionManifest }) {
  const { pages, menu, commands, indicators, settings } = manifest
  return (
    <Section id="extension-adds" title="What it adds to igni" caption="What people see in igni once it is installed.">
      <Part title="Pages" count={pages.length}>
        {pages.map((page) => (
          <li key={page.kind}>
            <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
              <Text weight="semibold">{page.label}</Text>
              <Code>{page.kind}</Code>
              {page.sidebar && <Label variant="secondary">In the sidebar</Label>}
            </Stack>
          </li>
        ))}
      </Part>
      <Part title="Menu entries" count={menu.length}>
        {menu.map((entry) => (
          <li key={`${entry.label}-${entry.command ?? ''}`}>
            <Text weight="semibold">{entry.label}</Text>
            {entry.command && (
              <>
                {' '}
                <Code>{entry.command}</Code>
              </>
            )}
            {entry.desc && <Text className="settings-section-caption"> — {entry.desc}</Text>}
          </li>
        ))}
      </Part>
      <Part title="Commands" count={commands.length}>
        {commands.map((command) => (
          <li key={command.command}>
            <Code>{command.command}</Code>
            {command.desc && <Text className="settings-section-caption"> — {command.desc}</Text>}
          </li>
        ))}
      </Part>
      <Part title="Status indicators" count={indicators.length}>
        {indicators.map((indicator) => (
          <li key={indicator.id}>
            <Stack gap="none">
              <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
                <Text weight="semibold">{indicator.label}</Text>
                <Code>{indicator.id}</Code>
              </Stack>
              <Text size="small" className="settings-section-caption">
                {[
                  indicator.opens ? `Opens ${indicator.opens}` : null,
                  indicator.intervalSeconds !== null ? `refreshes every ${indicator.intervalSeconds} seconds` : null,
                ]
                  .filter(Boolean)
                  .join('; ') || 'Shown in the status bar'}
              </Text>
            </Stack>
          </li>
        ))}
      </Part>
      <Part title="Settings" count={settings.length}>
        {settings.map((setting) => (
          <li key={setting.key}>
            <Stack gap="none">
              <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
                <Text weight="semibold">{setting.label ?? setting.key}</Text>
                <Code>{setting.key}</Code>
                {setting.type && <Label variant="secondary">{setting.type}</Label>}
              </Stack>
              <Text size="small" className="settings-section-caption">
                Default: <Code>{shownDefault(setting.default)}</Code>
              </Text>
            </Stack>
          </li>
        ))}
      </Part>
    </Section>
  )
}

function WhatItMayTouch({ manifest }: { manifest: ExtensionManifest }) {
  const p = manifest.permissions
  return (
    <Section
      id="extension-touches"
      title="What it may touch"
      caption="Declared by the extension. igni enforces what its pages and the agent may reach on each machine; its own process is the service's code, and is trusted rather than confined."
    >
      <Facts
        label="Workspace"
        rows={[
          ['Workspace', WORKSPACE_LABEL[p.workspace]],
          ...(p.workspace !== 'none'
            ? ([['Within', p.workspacePaths.length > 0 ? <Code>{p.workspacePaths.join(', ')}</Code> : 'The whole project']] as [
                string,
                ReactNode,
              ][])
            : []),
        ]}
      />
      <Stack gap="condensed">
        <Part title="Network egress" count={p.egress.length}>
          {p.egress.map((host) => (
            <li key={host}>
              <Code>{host}</Code>
            </li>
          ))}
        </Part>
        <Text size="small" className="settings-section-caption">
          What its pages may connect to, besides its own service — igni sets their content security policy from this
          list. Its process on each machine is not limited by it: approving this service is trusting its code.
        </Text>
      </Stack>
      <Part title="Secrets" count={p.secrets.length}>
        {p.secrets.map((secret) => (
          <li key={secret}>
            <Code>{secret}</Code>
          </li>
        ))}
      </Part>
      <Stack gap="condensed">
        <Part title="Tools for the agent" count={p.tools.length}>
          {p.tools.map((tool) => (
            <li key={tool.name}>
              <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
                <Code>{tool.name}</Code>
                <Text size="small" className="settings-section-caption">
                  {describeHints(tool) ?? 'does not state all four hints'}
                </Text>
              </Stack>
            </li>
          ))}
        </Part>
        {p.tools.length > 0 && (
          <Text size="small" className="settings-section-caption">
            {p.toolAudience === 'own-agents'
              ? 'Only the agents in its own plugin may call these.'
              : 'Any agent may call these.'}
          </Text>
        )}
      </Stack>
      <Part title="What it may reach in igni" count={p.host.length}>
        {p.host.map((grant) => (
          <li key={grant}>
            <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
              <Code>{grant}</Code>
              {HOST_GRANT_NOTE[grant] && (
                <Text size="small" className="settings-section-caption">
                  {HOST_GRANT_NOTE[grant]}
                </Text>
              )}
            </Stack>
          </li>
        ))}
      </Part>
      <Part title="igni's hook events it answers" count={p.hooks.length}>
        {p.hooks.map((hook) => (
          <li key={hook.event}>
            <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
              <Code>{hook.event}</Code>
              <Text size="small" className="settings-section-caption">
                {hook.decides
                  ? 'Asked before it happens: may stop it, or change it.'
                  : 'Told when it happens; nothing waits on it.'}
              </Text>
            </Stack>
          </li>
        ))}
      </Part>
      <Part title="Events it is told about" count={p.events.length}>
        {p.events.map((event) => (
          <li key={event}>
            <Code>{event}</Code>
          </li>
        ))}
      </Part>
      <Part title="Calls its pages may make" count={p.rpc.length}>
        {p.rpc.map((method) => (
          <li key={method}>
            <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
              <Code>{method}</Code>
              {manifest.reachesIgni(method) && <Label variant="attention">igni&apos;s own data</Label>}
            </Stack>
          </li>
        ))}
      </Part>
    </Section>
  )
}

function WhatItShips({ manifest }: { manifest: ExtensionManifest }) {
  const assets = manifest.assets
  return (
    <Section
      id="extension-ships"
      title="What it ships"
      caption="Files it runs that are not Python, carried beside its bundle so a machine with no network can install it. Each is pinned by size and SHA-256 in the signed manifest, checked here on upload and again on every machine; a machine downloads only its own platform's."
    >
      <Part title="Assets" count={assets.length}>
        {assets.map((asset) => (
          <li key={`${asset.name}-${asset.platform}`}>
            <Stack gap="none">
              <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
                <Text weight="semibold">{asset.name}</Text>
                <Text className="settings-section-caption">{PLATFORM_LABEL[asset.platform] ?? asset.platform}</Text>
                <Code>{asset.file}</Code>
                <Text size="small" className="settings-section-caption">
                  {shownSize(asset.size)}
                  {asset.unpack ? ', unpacked on install' : ''}
                </Text>
              </Stack>
              <Text size="small" className="settings-section-caption">
                SHA-256 <Code>{asset.sha256 ?? 'not stated'}</Code>
                {asset.source && (
                  <>
                    {' '}
                    from <Code>{asset.source}</Code>
                  </>
                )}
              </Text>
            </Stack>
          </li>
        ))}
      </Part>
    </Section>
  )
}

function ItsPlugin({ plugin }: { plugin: ExtensionPluginContents }) {
  return (
    <Section
      id="extension-plugin"
      title="Its plugin"
      caption="Agents and skills it adds to igni, under its own name. They never take the place of igni's own — that is a stand-in, listed with what it changes in igni's prompts."
    >
      <Part title="Agents" count={plugin.agents.length}>
        {plugin.agents.map((agent) => (
          <li key={agent.file ?? agent.name}>
            <Stack gap="none">
              <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
                <Text weight="semibold">{agent.name}</Text>
                <Label variant="secondary">New agent</Label>
              </Stack>
              {agent.description && <Text size="small">{agent.description}</Text>}
              {agent.file && (
                <Text size="small" className="settings-section-caption">
                  <Code>{agent.file}</Code>
                </Text>
              )}
            </Stack>
          </li>
        ))}
      </Part>
      <Part title="Skills" count={plugin.skills.length}>
        {plugin.skills.map((skill) => (
          <li key={skill.name}>
            <Stack gap="none">
              <Text weight="semibold">{skill.name}</Text>
              {skill.description && <Text size="small">{skill.description}</Text>}
            </Stack>
          </li>
        ))}
      </Part>
    </Section>
  )
}

/** "while `index-covers-head` holds", for a contribution that waits on a condition. */
function While({ when }: { when: string | null }) {
  if (!when) return null
  return (
    <Text size="small" className="settings-section-caption">
      {' '}
      — only while <Code>{when}</Code> holds
    </Text>
  )
}

function From({ source, from }: { source: string | null; from: string | null }) {
  if (!source && !from) return null
  return (
    <Text size="small" className="settings-section-caption">
      {' '}
      — from {source === 'tool' ? 'its tool' : (source ?? 'its')} {from && <Code>{from}</Code>}
    </Text>
  )
}

/** `main`, `plan_mode`, `agent:explorer`, as a person reads them. */
function promptName(target: string): string {
  if (target === 'main') return "igni's main prompt"
  if (target === 'plan_mode') return 'what igni tells the agent about plan mode'
  return `the ${target.slice('agent:'.length)} agent`
}

/** One edit, as a sentence: what it does, and where. */
function editSentence(edit: ManifestPromptEdit): string {
  if (edit.op === 'replace') return `replaces the section ${edit.section}`
  if (edit.op === 'insert') return `adds the section ${edit.name} ${edit.position} ${edit.section}`
  return `adds the section ${edit.name} at the end`
}

function WhatItDoesToThePrompts({ manifest, plugin }: { manifest: ExtensionManifest; plugin: ExtensionPluginContents }) {
  const { overrides, edits } = manifest.prompt
  const described = new Map(plugin.agentOverrides.map((entry) => [entry.agent, entry]))
  const targets = [...new Set(edits.map((edit) => edit.target))]
  return (
    <Section
      id="extension-prompt"
      title="What it changes in igni's prompts"
      caption="Per prompt — the main prompt, plan mode, any agent — an extension either overrides it whole or edits it. Edits from several extensions stack, in the order set on the Services page; another extension's override is the new starting point, and an edit aimed at a section that override does not have is left out."
    >
      <Part title="Prompts it overrides" count={overrides.length}>
        {overrides.map((override) => {
          const agent = override.target.startsWith('agent:') ? described.get(override.target.slice('agent:'.length)) : undefined
          return (
            <li key={override.target} className="service-replaces">
              <Stack gap="none">
                <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
                  <Code>{override.target}</Code>
                  <Label variant="attention">Its own {promptName(override.target)}</Label>
                  {override.exclusive && <Label variant="severe">Takes no one else&apos;s edits</Label>}
                </Stack>
                {agent?.description && <Text size="small">{agent.description}</Text>}
                <Text size="small" className="settings-section-caption">
                  Its sections: {override.sections.join(', ')}
                  <From source={override.source} from={override.from} />
                  <While when={override.when} />
                </Text>
              </Stack>
            </li>
          )
        })}
      </Part>
      <Part title="Prompts it edits" count={edits.length}>
        {targets.map((target) => (
          <li key={target}>
            <Stack gap="none">
              <Code>{target}</Code>
              {edits
                .filter((edit) => edit.target === target)
                .map((edit) => (
                  <Text key={`${edit.op} ${edit.section} ${edit.name}`} size="small" className="settings-section-caption">
                    It {editSentence(edit)}
                    <From source={edit.source} from={edit.from} />
                    <While when={edit.when} />
                  </Text>
                ))}
            </Stack>
          </li>
        ))}
      </Part>
      <Part title="Prompt fragments" count={manifest.promptFragments.length}>
        {manifest.promptFragments.map((fragment) => (
          <li key={fragment.anchor}>
            <Code>{fragment.anchor}</Code>
            <From source={fragment.source} from={fragment.from} />
          </li>
        ))}
      </Part>
    </Section>
  )
}

/**
 * Where this extension and another want the same thing — a whole prompt, a
 * prompt section, a command, an anchor — and who has it; and the edits that
 * land nowhere because an override leaves their section out.
 *
 * Nobody gets a contested thing by arriving first, and nobody decides it
 * here: the extension higher in the priority order on the Services page
 * has it, on every machine, and nothing is held back. To change who has
 * it, reorder the priority.
 */
function ItsCollisions({ review }: { review: ExtensionReview }) {
  return (
    <Section
      id="extension-collisions"
      title="Where it collides with other extensions"
      caption="Things only one extension can have. Two that want the same one collide, and the one higher in the priority order on the Services page has it — for everybody."
    >
      {review.unknown_targets.length > 0 && (
        <Banner
          variant="warning"
          title="It names parts of igni's prompts this igni does not have"
          description={`Its text for ${review.unknown_targets.join(', ')} would land nowhere.`}
        />
      )}
      {review.igni_commands.length > 0 && (
        <Banner
          variant="critical"
          title="It declares igni's own commands"
          description={`igni answers ${review.igni_commands.join(', ')} itself, so the extension's never run. igni switches them off.`}
        />
      )}
      {review.switched_off.length > 0 && (
        <Banner
          variant="warning"
          title="It loses some of what it wants"
          description={review.switched_off.map((off) => `${off.claim}: ${off.reason}`).join(' · ')}
        />
      )}
      {review.dropped.length > 0 && (
        <Banner
          variant="warning"
          title="Some of its edits land nowhere"
          description={review.dropped.map((drop) => `${drop.claim}: ${drop.reason}`).join(' · ')}
        />
      )}
      {Object.keys(review.drops_others).length > 0 && (
        <Banner
          variant="warning"
          title="Its override leaves out other extensions' edits"
          description={Object.entries(review.drops_others)
            .map(([extension, drops]) => `${extension}: ${drops.map((drop) => drop.claim).join(', ')}`)
            .join(' · ')}
        />
      )}
      <Part title="Collisions" count={review.collisions.length}>
        {review.collisions.map((collision) => (
          <li key={`${collision.claim} ${collision.extensions.join(' ')}`}>
            <Stack gap="condensed">
              <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
                <Code>{collision.claim}</Code>
                <Text size="small">between {collision.extensions.join(', ')}</Text>
              </Stack>
              <Text size="small" className="settings-section-caption">
                {collision.winner ? `${collision.winner} has it by priority` : 'Nobody holds the whole prompt'}
                {collision.losers.length > 0 && ` — ${collision.losers.join(', ')} lose${collision.losers.length === 1 ? 's' : ''} it`}
              </Text>
            </Stack>
          </li>
        ))}
      </Part>
    </Section>
  )
}

/** The manifest exactly as the service sent it, for whatever the sections above do not show. */
function AsSent({ manifest }: { manifest: Record<string, unknown> }) {
  return (
    <Section id="extension-manifest" title="The manifest as sent">
      <details className="service-manifest">
        <summary>Show the whole manifest</summary>
        <pre>{JSON.stringify(manifest, null, 2)}</pre>
      </details>
    </Section>
  )
}

function TheExtension({ extension }: { extension: ServiceExtensionDetail }) {
  const manifest = new ExtensionManifest(extension.manifest)
  const plugin = new ExtensionPluginContents(extension.plugin)
  const { overrides, edits } = manifest.prompt
  const replaced = edits.filter((edit) => edit.op === 'replace').map((edit) => `${edit.target}#${edit.section}`)
  const takesOver = [
    ...overrides.map((override) => `own ${promptName(override.target)}`),
    ...(replaced.length > 0
      ? [`${replaced.length === 1 ? 'version of one section' : `versions of ${replaced.length} sections`} of igni's prompts (${replaced.join(', ')})`]
      : []),
  ]

  return (
    <Stack gap="normal">
      {takesOver.length > 0 && (
        <Banner
          variant="warning"
          title="It takes over part of igni"
          description={`While it runs, people get its ${takesOver.join('; ')} instead of igni's.`}
        />
      )}
      <WhatItIs extension={extension} manifest={manifest} />
      {extension.review && <ItsCollisions review={extension.review} />}
      <WhatItAdds manifest={manifest} />
      <WhatItMayTouch manifest={manifest} />
      <WhatItShips manifest={manifest} />
      <WhatItDoesToThePrompts manifest={manifest} plugin={plugin} />
      <ItsPlugin plugin={plugin} />
      <AsSent manifest={extension.manifest} />
    </Stack>
  )
}

/**
 * One registered service, and everything its extension would put on
 * people's machines.
 *
 * The extension is the reason to come here before approving: approving a
 * service sends it to every signed-in machine in every organisation. So
 * this lays out what the manifest says — what it adds, what it may
 * touch, what it ships, what of igni it takes over, where it collides —
 * next to the thumbprint that says the registration is the service's at
 * all.
 */
export function ServiceDetailPage() {
  const { clientId = '' } = useParams<{ clientId: string }>()
  const navigate = useNavigate()
  const { decide, request, dialog } = useServiceDecision({ onRemoved: () => navigate('/services') })

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const detail = useQuery({
    queryKey: ['registered-client', clientId],
    queryFn: () => {
      // Written out whole, so the route check can match it.
      const id = encodeURIComponent(clientId)
      return client.get<RegisteredClientDetail>(`/portal/clients/${id}`).then((r) => r.data)
    },
    enabled: me.data?.is_super_admin === true && clientId !== '',
  })
  useDocumentTitle(detail.data ? detail.data.name : 'Service')

  if (me.data && me.data.is_super_admin !== true) {
    return (
      <Blankslate>
        <Blankslate.Heading>Super admins only</Blankslate.Heading>
        <Blankslate.Description>This page is for the people who own this igni install.</Blankslate.Description>
      </Blankslate>
    )
  }

  if (detail.isError) {
    const missing = (detail.error as { response?: { status?: number } }).response?.status === 404
    return (
      <Stack gap="normal">
        <Breadcrumbs>
          <Breadcrumbs.Item href="/services">Services</Breadcrumbs.Item>
        </Breadcrumbs>
        {missing ? (
          <Blankslate>
            <Blankslate.Heading>No such service</Blankslate.Heading>
            <Blankslate.Description>
              It may have been removed. A service that is still running registers again when it next starts.
            </Blankslate.Description>
          </Blankslate>
        ) : (
          <Banner variant="critical" title="Could not load this service" description={refusalOf(detail.error)} />
        )}
        <div>
          <Button as={RouterLink} to="/services" leadingVisual={ArrowLeftIcon}>
            Back to services
          </Button>
        </div>
      </Stack>
    )
  }

  if (!detail.data) return <Spinner />

  const registration = detail.data
  const state = STATE_LABEL[registration.state]
  const pending = registration.state === 'pending'
  const extension = registration.extension_detail

  return (
    <Stack gap="spacious">
      <Breadcrumbs>
        <Breadcrumbs.Item href="/services">Services</Breadcrumbs.Item>
        <Breadcrumbs.Item selected>{registration.name}</Breadcrumbs.Item>
      </Breadcrumbs>

      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <ServerIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>{registration.name}</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Actions>
          <Stack direction="horizontal" gap="condensed">
            {registration.state !== 'approved' && (
              <Button
                variant={pending ? 'primary' : 'default'}
                onClick={() => request(registration, 'approve')}
                disabled={decide.isPending}
                aria-label={`Approve ${registration.name}`}
              >
                Approve
              </Button>
            )}
            {registration.state === 'approved' && (
              <Button
                variant="danger"
                onClick={() => request(registration, 'revoke')}
                disabled={decide.isPending}
                aria-label={`Revoke ${registration.name}`}
              >
                Revoke
              </Button>
            )}
            {registration.state !== 'approved' && (
              <Button
                variant="danger"
                onClick={() => request(registration, 'remove')}
                disabled={decide.isPending}
                aria-label={`Remove ${registration.name}`}
              >
                Remove
              </Button>
            )}
          </Stack>
        </PageHeader.Actions>
        <PageHeader.Description>
          <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
            <Label variant="secondary">{registration.kind === 'service' ? 'Service' : 'App'}</Label>
            <Label variant={state.variant}>{state.text}</Label>
            {registration.held_version && (
              <Label variant="attention">Update asks for more: {registration.held_version}</Label>
            )}
          </Stack>
        </PageHeader.Description>
      </PageHeader>

      {decide.isError && <Banner variant="critical" title="Not changed" description={refusalOf(decide.error)} />}

      <section
        aria-labelledby="service-thumbprint"
        className={`settings-card service-section${pending ? ' services-card-pending' : ''}`}
      >
        <Stack gap="condensed">
          <Heading as="h2" variant="small" id="service-thumbprint">
            Key thumbprint
          </Heading>
          <code className="services-thumbprint">{registration.thumbprint}</code>
          <Text size="small">
            Approve only if this matches, character for character, the thumbprint {registration.name} printed in its
            own log when it started. Anybody who can reach igni can register under any name; only the thumbprint says
            this registration, and the extension below, came from the service you deployed.
          </Text>
          <Stack gap="none">
            <Text size="small">
              Web:{' '}
              <Link className="services-detail" href={registration.base_url} target="_blank" rel="noopener noreferrer">
                {registration.base_url}
              </Link>
            </Text>
            {registration.redirect_uris.length > 0 && (
              <Text size="small">
                Returns people to:{' '}
                <span className="services-detail">{registration.redirect_uris.join(', ')}</span>
              </Text>
            )}
            <Text size="small">
              Client ID: <span className="services-detail">{registration.client_id}</span>
            </Text>
            <Text size="small" className="settings-section-caption">
              Registered <RelativeTime datetime={registration.created_at} />
              {registration.decided_at && (
                <>
                  {' '}
                  · {registration.state === 'revoked' ? 'revoked' : 'approved'}{' '}
                  <RelativeTime datetime={registration.decided_at} />
                </>
              )}
            </Text>
          </Stack>
        </Stack>
      </section>

      {registration.held_update && <HeldUpdatePanel held={registration.held_update} />}

      {extension ? (
        <TheExtension extension={extension} />
      ) : (
        <Blankslate>
          <Blankslate.Visual>
            <PackageIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading>Has not uploaded its extension yet</Blankslate.Heading>
          <Blankslate.Description>
            A service uploads its extension when it starts, and everything in it is shown here. Nothing reaches
            people&apos;s machines until the service is approved.
          </Blankslate.Description>
        </Blankslate>
      )}

      {dialog}
    </Stack>
  )
}
