/**
 * Inference — the customer's own GPU fleet, live, and how they manage it.
 *
 * A self-hosted deployment's inference capacity is their hardware and
 * their bill, which makes "how busy is it, and do we need another one" a
 * question they ask us rather than a provider. This page answers it.
 *
 * ## Everything is shown, and everything has a colour
 *
 * Nodes come from three places — a config file, cluster automation that
 * registers them, and this page — and they used to carry wildly
 * different amounts of liveness information. A registered node had a
 * heartbeat; a configured one had nothing at all, so a machine that had
 * been switched off looked exactly like one serving traffic.
 *
 * Now every node is probed regardless of source, and every row has a
 * green, amber or red dot with the reason next to it. Nothing is hidden
 * for failing: a node an admin added that cannot be reached stays on the
 * page and turns red, because "it is down" is a far better answer than
 * "it is gone".
 *
 * Admin-only. Base URLs describe the shape of a private network, and the
 * write actions add machines that every developer's prompts get sent to.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import {
  Banner,
  Button,
  Checkbox,
  ConfirmationDialog,
  Details,
  Dialog,
  FormControl,
  Label,
  PageHeader,
  ProgressBar,
  Stack,
  Text,
  TextInput,
  Token,
  Truncate,
} from "@primer/react";
import { Blankslate, SkeletonText } from "@primer/react/experimental";
import {
  ChevronRightIcon,
  PencilIcon,
  CpuIcon,
  DotFillIcon,
  PlusIcon,
  ServerIcon,
  TrashIcon,
} from "@primer/octicons-react";

import { useDocumentTitle } from "../hooks/useDocumentTitle";
import client from "../api/client";
import { StatCard } from "../components/StatCard";
import type {
  HealthState,
  InferenceStatus,
  NodeProbeResult,
  NodeStatus,
  UserMe,
} from "../types";
import "./InferencePage.css";
import { countOf } from "../utils/plural";

// Node counts change on a human timescale — someone starts a cluster —
// but in-flight counts change constantly, and a stale number is worse
// than no number on a page whose whole job is "how busy is it right
// now".
const REFRESH_MS = 10_000;

// ``unknown`` deliberately shares amber with ``degraded``. A fourth
// colour meaning "we haven't looked yet" is not information an operator
// asking "is my cluster up?" can act on.
const HEALTH_TONE: Record<HealthState, "ok" | "warn" | "bad"> = {
  healthy: "ok",
  degraded: "warn",
  unknown: "warn",
  unreachable: "bad",
};

const HEALTH_LABEL: Record<HealthState, string> = {
  healthy: "Reachable",
  degraded: "Degraded",
  unknown: "Not yet checked",
  unreachable: "Unreachable",
};

function percent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

/**
 * Where a node came from, and therefore where to go to change it.
 *
 * Detail rather than row: provenance is not state. It matters at the
 * moment you want to alter something, which is the moment you have
 * opened the node — and in the row it was a repeated pill saying the
 * same word about every node.
 */
const SOURCE_DETAIL: Record<NodeStatus["source"], string> = {
  static: "this deployment’s configuration — change it there and redeploy",
  registered:
    "the node itself, at startup — change it in whatever starts the cluster",
  pinned: "someone here, in the portal",
};

/**
 * An age, formatted from the server's own measurement.
 *
 * Deliberately not reconstructed into a timestamp: the API reports how
 * long ago the heartbeat arrived *by its clock*, and turning that back
 * into an instant would fold the browser's clock skew into a number
 * whose whole purpose is deciding whether a machine is alive.
 */
function formatAge(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

/**
 * When something happened, from a timestamp rather than an age.
 *
 * The opposite call from `formatAge` above, and for the opposite reason:
 * a drain is an instant the server recorded once, not a duration it
 * measures each time you ask. Rendered in the reader's own locale
 * because "who turned this off, and when" is read against the reader's
 * own day.
 */
function formatWhen(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime())
    ? "at an unknown time"
    : `on ${at.toLocaleString()}`;
}

/**
 * Add a node in two steps: say where it is, then say what to route to it.
 *
 * The address comes first because everything else can be read off the
 * node. Typing model ids by hand is how you end up with a node that is
 * never selected — a mistyped id does not error, it simply never matches
 * a request — and LoRA adapter names are whatever the operator passed to
 * ``--lora-modules``, which nobody remembers.
 *
 * So: paste the URL, press Check, and pick from what is actually there.
 */
function NodeDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [probe, setProbe] = useState<NodeProbeResult | null>(null);
  const [nodeId, setNodeId] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [maxConcurrent, setMaxConcurrent] = useState(64);
  const [multimodal, setMultimodal] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = useMutation({
    mutationFn: () =>
      client
        .post<NodeProbeResult>("/portal/inference/nodes/discover", {
          base_url: baseUrl,
          api_key: apiKey || undefined,
        })
        .then((r) => r.data),
    onSuccess: (result) => {
      setProbe(result);
      // Everything it serves, selected by default: the common case is a
      // node whose whole model list should be routable.
      setModels(result.models);
      // A sensible name from the host, which the admin can overwrite.
      if (!nodeId) {
        try {
          setNodeId(new URL(baseUrl).hostname.split(".")[0]);
        } catch {
          /* an unparseable URL is the server's problem to report */
        }
      }
    },
    onError: () => setError("Could not reach that address."),
  });

  const add = useMutation({
    mutationFn: () =>
      client
        .post("/portal/inference/nodes", {
          node_id: nodeId,
          base_url: baseUrl,
          models,
          api_key: apiKey || undefined,
          max_concurrent: maxConcurrent,
          multimodal,
        })
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inference-status"] });
      onClose();
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : "Could not add the node.");
    },
  });

  const toggle = <T,>(
    list: T[],
    value: T,
    setter: (next: T[]) => void,
    atLeastOne = true,
  ) => {
    const next = list.includes(value)
      ? list.filter((v) => v !== value)
      : [...list, value];
    // A node routed to for no model is an entry that
    // sits in the list doing nothing.
    if (atLeastOne && next.length === 0) return;
    setter(next);
  };

  return (
    <Dialog title="Add a GPU node" onClose={onClose} width="xlarge">
      <Stack gap="normal">
        {error && (
          <Banner
            variant="critical"
            title="Could not add the node"
            description={error}
          />
        )}

        <FormControl required>
          <FormControl.Label>Address</FormControl.Label>
          <Stack direction="horizontal" gap="condensed" align="center">
            <TextInput
              value={baseUrl}
              placeholder="http://gpu-rack-01.internal:8000/v1"
              onChange={(e) => {
                setBaseUrl(e.target.value);
                setProbe(null);
              }}
              block
              className="inference-url-field"
            />
            <Button
              onClick={() => {
                setError(null);
                check.mutate();
              }}
              loading={check.isPending}
              disabled={!baseUrl || check.isPending}
            >
              Check
            </Button>
          </Stack>
          <FormControl.Caption>
            The OpenAI-compatible endpoint. We ask it which models it serves.
          </FormControl.Caption>
        </FormControl>

        <FormControl>
          <FormControl.Label>Token</FormControl.Label>
          <TextInput
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            block
          />
          <FormControl.Caption>
            Only if the node sits behind an auth proxy. Most private-network
            vLLM nodes have none.
          </FormControl.Caption>
        </FormControl>

        {probe && probe.needs_credentials && (
          <Banner
            variant="warning"
            title="That node wants a token"
            description={
              <Text size="small">
                It answered but refused the request. A vLLM node started with{" "}
                <code>--api-key</code> protects <code>/v1</code>, which is what
                we ask. Put the token above and press Check again.
              </Text>
            }
          />
        )}

        {probe && !probe.reachable && (
          <Banner
            variant="warning"
            title="That address did not answer"
            description={
              <Text size="small">
                {probe.detail ?? "No response."} You can still add it — it will
                show as unreachable until it comes up.
              </Text>
            }
          />
        )}

        {probe && probe.reachable && !probe.needs_credentials && (
          <>
            <Banner
              variant="success"
              title={`Reachable — ${probe.models.length} model${probe.models.length === 1 ? "" : "s"}`}
              description={
                <Text size="small">
                  Pick which to route here. On a node started with{" "}
                  <code>--enable-lora</code> the adapters appear alongside the
                  base model.
                </Text>
              }
            />

            <Stack gap="condensed">
              {probe.models.map((name) => (
                <FormControl key={name}>
                  <Checkbox
                    checked={models.includes(name)}
                    onChange={() => toggle(models, name, setModels)}
                  />
                  <FormControl.Label>{name}</FormControl.Label>
                </FormControl>
              ))}
              {probe.models.length === 0 && (
                <Text size="small" className="inference-muted">
                  It answered but listed no models, so there is nothing to route
                  here yet.
                </Text>
              )}
            </Stack>
          </>
        )}

        {/* Everything below is independent of the probe, so it is not
            hidden behind one. Only the model list has to come from the
            node; what the node is *for* is the operator's decision, and
            burying it until a probe succeeded meant a node that was not
            up yet could not be configured at all. */}
        <FormControl required>
          <FormControl.Label>Name</FormControl.Label>
          <TextInput
            value={nodeId}
            onChange={(e) => setNodeId(e.target.value)}
            block
          />
          <FormControl.Caption>
            Must be unique across every source. Used to track this node&apos;s
            load.
          </FormControl.Caption>
        </FormControl>

        <FormControl>
          <FormControl.Label>Concurrent requests</FormControl.Label>
          <TextInput
            type="number"
            value={String(maxConcurrent)}
            onChange={(e) => setMaxConcurrent(Number(e.target.value) || 1)}
          />
          <FormControl.Caption>
            Roughly the vLLM instance&apos;s <code>--max-num-seqs</code>.
            Adapters share it — they share the GPU.
          </FormControl.Caption>
        </FormControl>

        <FormControl>
          <Checkbox
            checked={multimodal}
            onChange={(e) => setMultimodal(e.target.checked)}
          />
          <FormControl.Label>Accepts images</FormControl.Label>
        </FormControl>

        <Stack direction="horizontal" gap="condensed" justify="end">
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              setError(null);
              add.mutate();
            }}
            loading={add.isPending}
            disabled={!probe || !nodeId || models.length === 0 || add.isPending}
          >
            Add node
          </Button>
        </Stack>
      </Stack>
    </Dialog>
  );
}

/**
 * Load colour by headroom: a node at 90% of eight slots is as full as one
 * at 90% of sixty-four.
 *
 * These are Primer token *names*, not CSS. ProgressBar splits ``bg`` on
 * the dot and builds ``var(--bgColor-<role>-<variant>)`` itself, so
 * handing it a finished ``var(...)`` produces
 * ``var(--bgColor-var(--bgColor-danger-emphasis)-emphasis)`` — invalid,
 * silently ignored, and the bar renders grey.
 */
function loadColor(ratio: number): string {
  if (ratio >= 0.9) return "danger.emphasis";
  if (ratio >= 0.7) return "attention.emphasis";
  return "success.emphasis";
}

/**
 * A labelled bar with its value beside the label.
 *
 * Primer's guidance for ProgressBar is to pair it with text stating the
 * value, and to give it an aria-label that says what the number means —
 * a bar alone is decoration to a screen reader.
 */
function Gauge({
  label,
  readout,
  ratio,
  color,
  caption,
}: {
  label: string;
  readout: string;
  ratio: number;
  color: string;
  caption?: string;
}) {
  return (
    <Stack gap="condensed" padding="none">
      <Stack
        direction="horizontal"
        justify="space-between"
        align="baseline"
        gap="condensed"
      >
        <Text size="small" weight="semibold">
          {label}
        </Text>
        <Text size="small" className="inference-numeric">
          {readout}
        </Text>
      </Stack>
      <ProgressBar
        progress={Math.min(100, Math.max(0, ratio * 100))}
        bg={color}
        barSize="small"
        aria-label={`${label}: ${readout}`}
      />
      {caption && (
        <Text size="small" className="inference-muted">
          {caption}
        </Text>
      )}
    </Stack>
  );
}

/** One fact, as a term and its description. */
function Fact({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <>
      <Text as="dt" size="small" className="inference-muted">
        {term}
      </Text>
      <dd className="inference-fact-value">{children}</dd>
    </>
  );
}

/**
 * One node: a summary row that opens.
 *
 * A native ``<details>`` via Primer's Details, so the disclosure gets
 * keyboard handling, ``aria-expanded`` and the open/closed state for
 * free rather than from hand-written ARIA.
 *
 * The collapsed row carries only what you scan for — up or down, how
 * loaded, anything queued. Everything else is one click away, because a
 * table wide enough to hold it all pushed its most interesting column off
 * the edge of the window.
 */
function NodeRow({
  node,
  onRemove,
  onSetServing,
  busy,
  canManage,
}: {
  node: NodeStatus;
  onRemove: (id: string) => void;
  onSetServing: (id: string, serving: boolean) => void;
  busy: boolean;
  /** A super admin: the fleet serves every organisation (SER-32). */
  canManage: boolean;
}) {
  // Native <details> keeps its content in the DOM whether open or not.
  // Mirroring the state lets the body be rendered only when it is
  // actually shown — the page re-polls every ten seconds, and four
  // gauges per node for a fleet that is mostly collapsed is a lot of
  // React for something nobody is looking at.
  const [open, setOpen] = useState(false);
  const ratio =
    node.max_concurrent > 0 ? node.in_flight / node.max_concurrent : 0;
  const m = node.metrics;

  return (
    <Details
      className="inference-node"
      open={open}
      onToggle={(event) =>
        setOpen((event.currentTarget as HTMLDetailsElement).open)
      }
    >
      <Details.Summary className="inference-node-summary">
        <span className="inference-node-caret" aria-hidden="true">
          <ChevronRightIcon />
        </span>

        <DotFillIcon
          className={`inference-dot inference-dot--${HEALTH_TONE[node.health.state]}`}
          aria-label={
            node.health.detail
              ? `${HEALTH_LABEL[node.health.state]}: ${node.health.detail}`
              : HEALTH_LABEL[node.health.state]
          }
        />

        <Text size="small" weight="semibold" className="inference-node-name">
          {node.id}
        </Text>

        {/* Keeps its width whether or not it has anything in it, so a
            row that starts queueing does not push its own bar out of
            line with the rows that are not. */}
        <span className="inference-node-queue">
          {node.drain ? (
            <Label variant="secondary">not serving</Label>
          ) : m?.waiting ? (
            <Label variant="attention">{m.waiting} queued</Label>
          ) : null}
        </span>

        {/* A bar, because occupancy is a proportion and the eye reads a
            proportion faster than it divides two numbers. The percentage
            stays beside it: Primer's guidance is to pair a ProgressBar
            with its value, and a bar alone cannot say whether it is 30%
            of eight slots or of sixty-four. */}
        <span className="inference-node-load">
          <ProgressBar
            progress={Math.min(100, ratio * 100)}
            bg={loadColor(ratio)}
            barSize="small"
            inline
            aria-label={`${node.in_flight} of ${node.max_concurrent} in flight`}
          />
          <Text
            size="small"
            className="inference-numeric inference-node-percent"
          >
            {percent(ratio)}
          </Text>
        </span>

        {/* Last, because it is the least changeable thing on the row:
            what a node serves is a setting, where everything to its left
            is state. */}
        <Text size="small" className="inference-muted inference-node-models" title={node.models.join(", ")}>
          {node.models.length === 1 ? node.models[0] : countOf(node.models.length, "model")}
        </Text>
      </Details.Summary>

      {open && (
        <div className="inference-node-detail">
          <div className="inference-gauges">
            <Gauge
              label="Routed by igni"
              readout={`${node.in_flight} / ${node.max_concurrent}`}
              ratio={ratio}
              color={loadColor(ratio)}
              caption="leases igni issued — blind to any other client of this GPU"
            />
            {m?.running != null && (
              <Gauge
                label="Running on the node"
                readout={`${m.running} / ${node.max_concurrent}`}
                ratio={
                  node.max_concurrent > 0 ? m.running / node.max_concurrent : 0
                }
                color={
                  m.running === node.in_flight
                    ? loadColor(ratio)
                    : "attention.emphasis"
                }
                caption={
                  m.running === node.in_flight
                    ? "agrees with igni’s count"
                    : "differs from igni’s count, so something else is using this GPU"
                }
              />
            )}
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
                // Inverted: a high hit rate is the outcome we want, where a
                // high cache usage is the one we watch for.
                color={loadColor(1 - m.prefix_cache_hit_percent / 100)}
                caption="requests that reused a cached prefix — the payoff from sticky routing"
              />
            )}
          </div>

          <dl className="inference-facts">
            <Fact term="Address">
              <Truncate title={node.base_url} maxWidth={520} inline>
                <Text size="small" className="inference-numeric">
                  {node.base_url}
                </Text>
              </Truncate>
            </Fact>

            <Fact term="Models">
              <Stack
                direction="horizontal"
                gap="condensed"
                wrap="wrap"
                padding="none"
              >
                {node.models.map((name, i) => (
                  <Token
                    key={name}
                    text={name}
                    size="small"
                    title={i === 0 ? "base model" : "LoRA adapter"}
                  />
                ))}
              </Stack>
            </Fact>

            <Fact term="Status">
              <Text size="small">
                {HEALTH_LABEL[node.health.state]}
                {node.health.detail ? ` — ${node.health.detail}` : ""}
              </Text>
              {node.health.latency_ms != null && (
                <Text size="small" className="inference-muted">
                  {" "}
                  ({Math.round(node.health.latency_ms)}ms)
                </Text>
              )}
            </Fact>

            {m?.waiting != null && (
              <Fact term="Queued on the node">
                <Text size="small">
                  {m.waiting === 0
                    ? "nothing waiting"
                    : countOf(m.waiting, "request")}
                </Text>
                {m.waiting > 0 && (
                  <Text size="small" className="inference-muted">
                    {" "}
                    — piling up where igni cannot balance it
                  </Text>
                )}
              </Fact>
            )}

            {m?.preemptions != null && (
              <Fact term="Preemptions">
                <Text size="small">{m.preemptions} since start</Text>
                {m.preemptions > 0 && (
                  <Text size="small" className="inference-muted">
                    {" "}
                    — sequences discarded and redone for want of KV blocks
                  </Text>
                )}
              </Fact>
            )}

            {!m && (
              <Fact term="On the node">
                <Text size="small">no metrics published</Text>
                <Text size="small" className="inference-muted">
                  {" "}
                  — not vLLM, or /metrics is not exposed
                </Text>
              </Fact>
            )}

            {node.source === "registered" &&
              node.last_seen_seconds_ago != null && (
                <Fact term="Last heartbeat">
                  <Text size="small">
                    {formatAge(node.last_seen_seconds_ago)}
                  </Text>
                  <Text size="small" className="inference-muted">
                    {" "}
                    — registrations expire without one
                  </Text>
                </Fact>
              )}

            {/* First, because it is the only control on the panel that
              anybody opens this row *to use*. Available for every node
              however it got here: edit and remove change a node's
              definition, which only its owner can do, but this changes
              *routing*, which is this deployment's own business — and an
              operator stopping a GPU mid-incident should not first have
              to work out whether it registered itself. */}
            <Fact term="Serving">
              {/* Two named actions rather than a switch. A toggle leaves
                the reader working out which way it points — and Primer's
                puts its label on a wrapper rather than on the control,
                so the button had no accessible name either. "Stop
                serving" cannot be read backwards. */}
              <Stack
                direction="horizontal"
                gap="condensed"
                align="center"
                padding="none"
              >
                {canManage && (
                  <Button
                    size="small"
                    variant={node.drain ? "primary" : "default"}
                    disabled={busy}
                    onClick={() => onSetServing(node.id, node.drain != null)}
                  >
                    {node.drain ? `Resume ${node.id}` : `Stop serving ${node.id}`}
                  </Button>
                )}
                {node.drain ? (
                  <Text size="small" className="inference-muted">
                    stopped by {node.drain.by_email ?? node.drain.by}{" "}
                    {formatWhen(node.drain.at)}
                    {node.drain.reason ? ` — ${node.drain.reason}` : ""}
                  </Text>
                ) : (
                  <Text size="small" className="inference-muted">
                    in-flight requests finish; new ones stop immediately
                  </Text>
                )}
              </Stack>
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

          {/* Only nodes added here can be managed here: a configured node
            changes by redeploy, a registered one by whatever started the
            cluster. */}
          {canManage && node.source === "pinned" && (
            <Stack direction="horizontal" gap="condensed" padding="none">
              <Button
                size="small"
                leadingVisual={PencilIcon}
                as={RouterLink}
                to={`/inference/nodes/${node.id}/edit`}
              >
                Edit
              </Button>
              <Button
                variant="danger"
                size="small"
                leadingVisual={TrashIcon}
                onClick={() => onRemove(node.id)}
              >
                Remove node
              </Button>
            </Stack>
          )}
        </div>
      )}
    </Details>
  );
}

/** Every node, in a stable order. */
function nodesOf(status: InferenceStatus): NodeStatus[] {
  return [...status.nodes].sort((a, b) => a.id.localeCompare(b.id));
}

/** A number the page exists to deliver, in the portal's card idiom. */
function FleetSummary({ status }: { status: InferenceStatus }) {
  const nodes = nodesOf(status);
  const unwell = nodes.filter((n) => n.health.state !== "healthy");

  return (
    <Stack direction="horizontal" gap="normal" wrap="wrap">
      {/* Every card carries a sub-line, so they are the same height. The
          Nodes card had none and sat visibly shorter than its
          neighbours. */}
      <StatCard
        label="Nodes"
        value={String(nodes.length)}
        sub={
          nodes.length === 0
            ? "nothing configured yet"
            : `${nodes.reduce((total, n) => total + n.max_concurrent, 0)} concurrent in total`
        }
      />
      <StatCard
        label="Capacity in use"
        value={status.total_capacity > 0 ? percent(status.utilization) : "—"}
        sub={
          status.total_capacity > 0
            ? `${status.total_in_flight} of ${status.total_capacity} concurrent`
            : "no nodes"
        }
      />
      <StatCard
        label="Needs attention"
        value={String(unwell.length)}
        sub={
          unwell.length
            ? unwell.map((n) => n.id).join(", ")
            : "everything answering"
        }
        tone={unwell.length ? "bad" : undefined}
      />
    </Stack>
  );
}

/** The fleet: one row per GPU, whatever it serves. */
function FleetList({
  status,
  canManage,
}: {
  status: InferenceStatus;
  canManage: boolean;
}) {
  const queryClient = useQueryClient();
  const nodes = nodesOf(status);

  const remove = useMutation({
    mutationFn: (nodeId: string) =>
      client.delete(`/portal/inference/nodes/${nodeId}`),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["inference-status"] }),
  });

  const [confirmRemove, setConfirmRemove] = useState<NodeStatus | null>(null);

  // Two endpoints rather than a PATCH with a boolean, because the two
  // directions are not symmetrical: draining records who and when,
  // resuming clears a record. A single field would have to invent a
  // meaning for writing the value it already holds.
  const setServing = useMutation({
    mutationFn: ({ id, serving }: { id: string; serving: boolean }) =>
      serving
        ? client.delete(`/portal/inference/nodes/${id}/drain`)
        : client.post(`/portal/inference/nodes/${id}/drain`, {}),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["inference-status"] }),
  });

  if (nodes.length === 0) {
    return (
      <Blankslate>
        <Blankslate.Visual>
          <ServerIcon size={20} />
        </Blankslate.Visual>
        <Blankslate.Heading>No inference nodes</Blankslate.Heading>
        <Blankslate.Description>
          Nothing serves this deployment, so chat and indexing will both fail.
          Add a node above, put one in <code>inference_backends</code>, or have
          one register itself.
        </Blankslate.Description>
      </Blankslate>
    );
  }

  return (
    <Stack gap="condensed">
      <div className="inference-nodes" aria-label="Inference nodes">
        {/* A stack of native disclosures, already navigable — wrapping
            them in role="list"/"listitem" would add nothing a screen
            reader does not get from the details elements themselves. */}
        {nodes.map((node) => (
          <NodeRow
            key={node.id}
            node={node}
            onRemove={() => setConfirmRemove(node)}
            onSetServing={(id, serving) => setServing.mutate({ id, serving })}
            // The page polls every ten seconds, so without this a
            // control springs back to its old position for up to a
            // second before the refetch lands — which reads as the
            // click having failed.
            busy={setServing.isPending && setServing.variables?.id === node.id}
            canManage={canManage}
          />
        ))}
      </div>

      {/* Removing a node takes it out of rotation for everyone, and the
          same panel offers a checkbox that only changes what it serves —
          two very different outcomes a click apart. The portal confirms
          destructive actions elsewhere for the same reason. */}
      {confirmRemove && (
        <ConfirmationDialog
          title={`Remove ${confirmRemove.id}?`}
          confirmButtonContent="Remove node"
          cancelButtonContent="Cancel"
          confirmButtonType="danger"
          onClose={(gesture) => {
            const node = confirmRemove;
            setConfirmRemove(null);
            if (gesture === "confirm") remove.mutate(node.id);
          }}
        >
          <Text size="medium">
            It stops receiving requests immediately.{" "}
            {confirmRemove.in_flight > 0
              ? `${countOf(confirmRemove.in_flight, "request")} already in flight will finish.`
              : "Nothing is in flight."}{" "}
            The machine itself is untouched, and you can add it back with the
            same address.
          </Text>
        </ConfirmationDialog>
      )}
    </Stack>
  );
}

export function InferencePage() {
  useDocumentTitle("Inference");
  const [adding, setAdding] = useState(false);

  const statusQuery = useQuery({
    queryKey: ["inference-status"],
    queryFn: () =>
      client.get<InferenceStatus>("/portal/inference").then((r) => r.data),
    refetchInterval: REFRESH_MS,
  });

  const forbidden =
    (statusQuery.error as { response?: { status?: number } } | null)?.response
      ?.status === 403;

  // The fleet serves every organisation, so changing it is a super
  // admin's; an org admin sees it read-only (SER-32).
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: () => client.get<UserMe>("/portal/me").then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  });
  const canManage = meQuery.data?.is_super_admin === true;

  return (
    <Stack gap="normal">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Inference</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="small" className="inference-muted">
            The GPU nodes this deployment routes to, how busy they are, and
            whether they are answering.
            {!forbidden && meQuery.data && !canManage && (
              <>
                {" "}
                Only a super admin can add or change nodes: they serve every
                organisation.
              </>
            )}
          </Text>
        </PageHeader.Description>
        {!forbidden && canManage && (
          <PageHeader.Actions>
            <Button
              variant="primary"
              leadingVisual={PlusIcon}
              onClick={() => setAdding(true)}
            >
              Add node
            </Button>
          </PageHeader.Actions>
        )}
      </PageHeader>

      {forbidden && (
        <Blankslate spacious>
          <Blankslate.Visual>
            <CpuIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading>Admins only</Blankslate.Heading>
          <Blankslate.Description>
            Inference node statistics describe the internal network your GPUs
            sit on. Ask an organization admin if you need them.
          </Blankslate.Description>
        </Blankslate>
      )}

      {statusQuery.isError && !forbidden && (
        <Banner variant="critical" title="Could not load inference status">
          {(statusQuery.error as Error).message}
        </Banner>
      )}

      {statusQuery.isPending && <SkeletonText lines={4} />}

      {statusQuery.data && (
        <Stack gap="spacious">
          <FleetSummary status={statusQuery.data} />
          <FleetList status={statusQuery.data} canManage={canManage} />

          {/* "Nothing has registered" and "this deployment does not accept
              registrations" look identical from the list above, and they
              call for completely different fixes. */}
          <Text size="small" className="inference-muted">
            {statusQuery.data.registration_enabled
              ? `Nodes may also register themselves, and are expected to check in every ${statusQuery.data.heartbeat_interval_seconds}s. One that stops leaves rotation on its own.`
              : "Nodes cannot register themselves on this deployment. Set node_registration_token to let a GPU cluster announce itself when it starts."}
          </Text>
        </Stack>
      )}

      {adding && <NodeDialog onClose={() => setAdding(false)} />}
    </Stack>
  );
}
