import type { NodeStatus } from '../../types'
import { loadColor, occupancy } from './display'
import { Gauge } from './Gauge'

/** igni's own count beside every gauge the node itself reported. */
export function NodeGauges({ node }: { node: NodeStatus }) {
  const ratio = occupancy(node.in_flight, node.max_concurrent)
  const m = node.metrics

  return (
    <div className="inference-gauges">
      <Gauge
        label="Routed by igni"
        readout={`${node.in_flight} / ${node.max_concurrent}`}
        ratio={ratio}
        color={loadColor(ratio)}
        caption="leases igni issued — blind to any other client of this GPU"
      />
      {m?.running != null && <RunningGauge node={node} running={m.running} />}
      {m?.kv_cache_percent != null && (
        <Gauge
          label="KV cache"
          readout={`${m.kv_cache_percent}%`}
          ratio={m.kv_cache_percent / 100}
          color={loadColor(m.kv_cache_percent / 100)}
          caption="what sticky routing protects; eviction starts as this fills"
        />
      )}
      {m?.prefix_cache_hit_percent != null && (
        <Gauge
          label="Prefix cache hits"
          readout={`${m.prefix_cache_hit_percent}%`}
          ratio={m.prefix_cache_hit_percent / 100}
          // Inverted: a high hit rate is the outcome we want.
          color={loadColor(1 - m.prefix_cache_hit_percent / 100)}
          caption="requests that reused a cached prefix — the payoff from sticky routing"
        />
      )}
    </div>
  )
}

/** The node's own running count, amber when it disagrees with igni's. */
function RunningGauge({ node, running }: { node: NodeStatus; running: number }) {
  const agrees = running === node.in_flight
  return (
    <Gauge
      label="Running on the node"
      readout={`${running} / ${node.max_concurrent}`}
      ratio={occupancy(running, node.max_concurrent)}
      color={agrees ? loadColor(occupancy(node.in_flight, node.max_concurrent)) : 'attention.emphasis'}
      caption={
        agrees
          ? 'agrees with igni’s count'
          : 'differs from igni’s count, so something else is using this GPU'
      }
    />
  )
}
