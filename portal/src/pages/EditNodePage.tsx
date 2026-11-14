/**
 * Editing one node — a GPU node or a cloud backend — on its own page.
 *
 * The fleet list used to be editable in place: two checkboxes that
 * PATCHed on change, with no Save. That is a defensible shape for a
 * single toggle and the wrong one here — a node has many fields, and
 * changing "what it serves" without changing "how many at once" is not
 * the common case. Half-applied edits are also unreviewable: you cannot
 * see what you are about to do before it is done.
 *
 * So: the row is read-only, Edit comes here, everything changes
 * together, Save writes once. The form is the add dialog's draft
 * (`nodeDraft.ts`) and, for a cloud backend, its fields
 * (`CloudBackendFields`), so the two cannot drift apart.
 *
 * ## Only nodes added here
 *
 * A registered node is defined by whatever started the cluster and
 * re-announces itself every 60 seconds; a static one lives in the
 * deployment's config. Neither can be edited from a browser, so
 * `GET /portal/inference/nodes/{id}` answers 404 for them and this page
 * says so rather than offering a form that cannot save. Taking one out
 * of rotation is a different thing and stays on the list, for every node
 * however it arrived — see F117.
 */
import { ArrowLeftIcon } from "@primer/octicons-react";
import {
  Banner,
  Breadcrumbs,
  Button,
  Checkbox,
  FormControl,
  PageHeader,
  Spinner,
  Stack,
  Text,
  TextInput,
} from "@primer/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";

import client from "../api/client";
import { refusalOf } from "../api/refusal";
import {
  methodsOf,
  SECRET_STORED,
  useAuthMethods,
} from "../components/inference/auth";
import { CloudBackendFields } from "../components/inference/CloudBackendFields";
import { INFERENCE_STATUS_KEY } from "../components/inference/fleet";
import { isCloud, KINDS } from "../components/inference/kinds";
import {
  draftOf,
  draftProblem,
  type NodeDraft,
  pinnedNodeUpdateOf,
} from "../components/inference/nodeDraft";
import type { PinnedNode } from "../types";
import "./EditNodePage.css";

export function EditNodePage() {
  const { nodeId = "" } = useParams<{ nodeId: string }>();

  const query = useQuery<PinnedNode>({
    queryKey: ["inference-node", nodeId],
    queryFn: () =>
      client
        .get<PinnedNode>(`/portal/inference/nodes/${nodeId}`)
        .then((r) => r.data),
    retry: false,
  });

  if (query.isLoading) {
    return <Spinner aria-label="Loading the node" />;
  }

  if (query.isError || !query.data) {
    const notHere =
      isAxiosError(query.error) && query.error.response?.status === 404;
    return (
      <Stack gap="normal">
        <Breadcrumbs>
          <Breadcrumbs.Item href="/inference">Inference</Breadcrumbs.Item>
          <Breadcrumbs.Item selected>{nodeId}</Breadcrumbs.Item>
        </Breadcrumbs>
        {notHere ? (
          <Banner
            variant="warning"
            title="This node cannot be edited here"
            description={
              <Text size="small">
                It was not added from the portal. A registered node is defined
                by whatever started the cluster and re-announces itself every
                minute; a configured one lives in this deployment&apos;s
                settings. You can still take it out of rotation from the fleet
                list.
              </Text>
            }
          />
        ) : (
          <Banner
            variant="critical"
            title="The node could not be loaded"
            description={refusalOf(query.error, "Reload to try again.")}
          />
        )}
        <Button as={RouterLink} to="/inference">
          Back to inference
        </Button>
      </Stack>
    );
  }

  // Keyed on the node id so navigating between two nodes remounts the
  // form rather than showing the previous one's values.
  return <NodeForm key={nodeId} node={query.data} />;
}

/**
 * The form, mounted only once its row exists.
 *
 * Initialised from props rather than seeded by an effect — the effect
 * version is what eslint's `react-hooks/set-state-in-effect` refuses,
 * and it is right to: a render that sets state in an effect renders
 * once with empty fields and again with real ones, and any value the
 * admin has typed in between is discarded by the next refetch.
 */
function NodeForm({ node }: { node: PinnedNode }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const table = useAuthMethods();
  const [draft, setDraft] = useState<NodeDraft>(() => draftOf(node));
  const [error, setError] = useState<string | null>(null);
  const edit = (change: Partial<NodeDraft>) =>
    setDraft((current) => ({ ...current, ...change }));

  const cloud = isCloud(node.kind);
  const methods = methodsOf(table.data, node.kind);
  const method = methods.find((m) => m.method === draft.auth.method);
  // Only the stored method's secret can be kept by leaving the field blank;
  // a newly chosen method has none stored yet.
  const secretStored =
    draft.auth.method === node.auth &&
    (method?.secret === "api_key" ? node.has_api_key : node.has_credential);

  const save = useMutation({
    mutationFn: () =>
      client.patch(
        `/portal/inference/nodes/${node.node_id}`,
        pinnedNodeUpdateOf(draft, method),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: INFERENCE_STATUS_KEY });
      await queryClient.invalidateQueries({
        queryKey: ["inference-node", node.node_id],
      });
      navigate("/inference");
    },
    onError: (err) => setError(refusalOf(err, "The node was not saved.")),
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    // Checked here as well as by the API, so the answer comes first.
    const found = draftProblem(draft, method, { adding: false, secretStored });
    setError(found);
    if (!found) save.mutate();
  }

  return (
    <Stack gap="spacious">
      <Breadcrumbs>
        <Breadcrumbs.Item href="/inference">Inference</Breadcrumbs.Item>
        <Breadcrumbs.Item selected>{node.node_id}</Breadcrumbs.Item>
      </Breadcrumbs>

      <PageHeader>
        <PageHeader.TitleArea>
          <Button
            variant="invisible"
            as={RouterLink}
            to="/inference"
            aria-label="Back to inference"
          >
            <ArrowLeftIcon />
          </Button>
          <PageHeader.Title as="h1">{node.node_id}</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="medium">
            {KINDS[node.kind].label}. Nothing changes until you press Save.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {error && (
        <Banner variant="critical" title="Not saved" description={error} />
      )}
      {cloud && table.isError && (
        <Banner
          variant="critical"
          title="How this provider signs in could not be loaded"
          description="The server's list of auth methods did not load, so this backend cannot be saved. Reload to try again."
        />
      )}

      <form onSubmit={submit}>
        <Stack direction="vertical" gap="normal" className="node-form">
          {cloud ? (
            <CloudBackendFields
              draft={draft}
              methods={methods}
              onChange={edit}
              secretStored={secretStored}
            />
          ) : (
            <GpuNodeEditFields draft={draft} node={node} onChange={edit} />
          )}

          <FormControl required>
            <FormControl.Label>Concurrency</FormControl.Label>
            <TextInput
              type="number"
              min={1}
              value={String(draft.maxConcurrent)}
              onChange={(e) =>
                edit({ maxConcurrent: Number(e.currentTarget.value) })
              }
            />
            <FormControl.Caption>
              How many requests igni will have in flight here at once. It cannot
              see any other client of the same backend.
            </FormControl.Caption>
          </FormControl>

          <FormControl>
            <Checkbox
              checked={draft.multimodal}
              onChange={(e) => edit({ multimodal: e.currentTarget.checked })}
            />
            <FormControl.Label>Accepts images</FormControl.Label>
          </FormControl>

          <FormControl>
            <Checkbox
              checked={draft.enabled}
              onChange={(e) => edit({ enabled: e.currentTarget.checked })}
            />
            <FormControl.Label>Enabled</FormControl.Label>
            <FormControl.Caption>
              Off removes it from routing entirely. For a temporary stop —
              before maintenance, say — use Stop serving on the fleet list,
              which records who did it and why.
            </FormControl.Caption>
          </FormControl>

          <Stack direction="horizontal" gap="condensed" padding="none">
            <Button
              type="submit"
              variant="primary"
              disabled={save.isPending || (cloud && !method)}
            >
              {save.isPending ? "Saving…" : "Save node"}
            </Button>
            <Button as={RouterLink} to="/inference" disabled={save.isPending}>
              Cancel
            </Button>
          </Stack>
        </Stack>
      </form>
    </Stack>
  );
}

/** A GPU node's address, models and key: what a cloud backend's fields are to a cloud. */
function GpuNodeEditFields({
  draft,
  node,
  onChange,
}: {
  draft: NodeDraft;
  node: PinnedNode;
  onChange: (change: Partial<NodeDraft>) => void;
}) {
  return (
    <>
      <FormControl required>
        <FormControl.Label>Address</FormControl.Label>
        <TextInput
          value={draft.baseUrl}
          onChange={(e) => onChange({ baseUrl: e.currentTarget.value })}
          block
        />
        <FormControl.Caption>
          Where igni sends requests, including the <code>/v1</code> path if the
          node serves one.
        </FormControl.Caption>
      </FormControl>

      <FormControl required>
        <FormControl.Label>Models</FormControl.Label>
        <TextInput
          value={draft.modelsText}
          onChange={(e) => onChange({ modelsText: e.currentTarget.value })}
          block
        />
        <FormControl.Caption>
          Comma-separated, and every id has to match what the node answers to —
          a mistyped one does not error, it simply never matches a request. On a
          LoRA node list the adapters too.
        </FormControl.Caption>
      </FormControl>

      <FormControl>
        <FormControl.Label>API key</FormControl.Label>
        <TextInput
          type="password"
          value={draft.apiKey}
          onChange={(e) => onChange({ apiKey: e.currentTarget.value })}
          placeholder={node.has_api_key ? SECRET_STORED : "none set"}
          block
        />
        <FormControl.Caption>
          Sent as a bearer token to the node. It is never returned, so leaving
          this blank keeps whatever is stored.
        </FormControl.Caption>
      </FormControl>
    </>
  );
}

export default EditNodePage;
