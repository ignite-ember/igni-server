import { Stack, Text, Token, Truncate } from '@primer/react'

import type { NodeStatus } from '../../types'
import { countOf } from '../../utils/plural'
import { KIND_LABEL } from './auth'
import { formatAge, HEALTH_LABEL, SOURCE_DETAIL } from './display'
import { Fact, Glossed } from './Fact'
import { ServingControl } from './ServingControl'

/** Everything about a node that is not a gauge. */
export function NodeFacts({
  node,
  canManage,
  busy,
  onSetServing,
}: {
  node: NodeStatus
  canManage: boolean
  busy: boolean
  onSetServing: (id: string, serving: boolean) => void
}) {
  const m = node.metrics

  return (
    <dl className="inference-facts">
      <Fact term="Address">
        <Truncate title={node.base_url} maxWidth={520} inline>
          <Text size="small" className="inference-numeric">
            {node.base_url}
          </Text>
        </Truncate>
      </Fact>

      {node.kind && node.kind !== 'openai' && (
        <Fact term="Provider">
          <Text size="small">{KIND_LABEL[node.kind]}</Text>
        </Fact>
      )}

      <Fact term="Models">
        <Stack direction="horizontal" gap="condensed" wrap="wrap" padding="none">
          {node.models.map((name, i) => (
            <Token key={name} text={name} size="small" title={i === 0 ? 'base model' : 'LoRA adapter'} />
          ))}
        </Stack>
      </Fact>

      <Fact term="Status">
        <Glossed
          value={`${HEALTH_LABEL[node.health.state]}${node.health.detail ? ` — ${node.health.detail}` : ''}`}
          gloss={node.health.latency_ms != null && `(${Math.round(node.health.latency_ms)}ms)`}
        />
      </Fact>

      {m?.waiting != null && (
        <Fact term="Queued on the node">
          <Glossed
            value={m.waiting === 0 ? 'nothing waiting' : countOf(m.waiting, 'request')}
            gloss={m.waiting > 0 && '— piling up where igni cannot balance it'}
          />
        </Fact>
      )}

      {m?.preemptions != null && (
        <Fact term="Preemptions">
          <Glossed
            value={`${m.preemptions} since start`}
            gloss={m.preemptions > 0 && '— sequences discarded and redone for want of KV blocks'}
          />
        </Fact>
      )}

      {!m && (
        <Fact term="On the node">
          <Glossed value="no metrics published" gloss="— not vLLM, or /metrics is not exposed" />
        </Fact>
      )}

      {node.source === 'registered' && node.last_seen_seconds_ago != null && (
        <Fact term="Last heartbeat">
          <Glossed value={formatAge(node.last_seen_seconds_ago)} gloss="— registrations expire without one" />
        </Fact>
      )}

      <Fact term="Serving">
        <ServingControl node={node} canManage={canManage} busy={busy} onSetServing={onSetServing} />
      </Fact>

      <Fact term="Added by">
        <Text size="small">{SOURCE_DETAIL[node.source]}</Text>
      </Fact>

      {node.multimodal && (
        <Fact term="Images">
          <Text size="small">accepted</Text>
        </Fact>
      )}
    </dl>
  )
}
