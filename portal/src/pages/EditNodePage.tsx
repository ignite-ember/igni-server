/**
 * Editing one GPU node, on its own page.
 *
 * The fleet list used to be editable in place: two checkboxes that
 * PATCHed on change, with no Save. That is a defensible shape for a
 * single toggle and the wrong one here — a node has seven fields, and
 * changing "what it serves" without changing "how many at once" is not
 * the common case. Half-applied edits are also unreviewable: you cannot
 * see what you are about to do before it is done.
 *
 * So: the row is read-only, Edit comes here, everything changes
 * together, Save writes once. The same shape as a group entry
 * (`EditEntryPage`), which is the pattern this portal already uses for
 * anything with more than one field.
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
  Textarea,
  PageHeader,
  Spinner,
  Stack,
  Text,
  TextInput,
} from "@primer/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";

import client from "../api/client";
import {
  type AuthDraft,
  authComplete,
  authFields,
  isVertex,
  KIND_LABEL,
  methodsOf,
  modelIdsText,
  parseModelIds,
  useAuthMethods,
} from "../components/inference/auth";
import { AuthFields } from "../components/inference/AuthFields";
import type { PinnedNode } from "../types";
import "./EditNodePage.css";

type StoredNode = PinnedNode;

export function EditNodePage() {
  const { nodeId = "" } = useParams<{ nodeId: string }>();

  const query = useQuery<StoredNode>({
    queryKey: ["inference-node", nodeId],
    queryFn: () =>
      client.get(`/portal/inference/nodes/${nodeId}`).then((r) => r.data),
    retry: false,
  });

  if (query.isLoading) {
    return <Spinner aria-label="Loading the node" />;
  }

  if (query.isError || !query.data) {
    return (
      <Stack gap="normal">
        <Breadcrumbs>
          <Breadcrumbs.Item href="/inference">Inference</Breadcrumbs.Item>
          <Breadcrumbs.Item selected>{nodeId}</Breadcrumbs.Item>
        </Breadcrumbs>
        <Banner
          variant="warning"
          title="This node cannot be edited here"
          description={
            <Text size="small">
              It was not added from the portal. A registered node is defined by
              whatever started the cluster and re-announces itself every minute;
              a configured one lives in this deployment&apos;s settings. You can
              still take it out of rotation from the fleet list.
            </Text>
          }
        />
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
function NodeForm({ node }: { node: StoredNode }) {
  const nodeId = node.node_id;
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [baseUrl, setBaseUrl] = useState(node.base_url);
  const [models, setModels] = useState(node.models.join(", "));
  const [maxConcurrent, setMaxConcurrent] = useState(
    String(node.max_concurrent),
  );
  const [multimodal, setMultimodal] = useState(node.multimodal);
  const [enabled, setEnabled] = useState(node.enabled);
  const [apiKey, setApiKey] = useState("");
  const [region, setRegion] = useState(node.region ?? "");
  const [project, setProject] = useState(node.project ?? "");
  const [modelIds, setModelIds] = useState(modelIdsText(node.model_ids ?? {}));
  const [auth, setAuth] = useState<AuthDraft>({
    method: node.auth,
    settings: node.auth_settings ?? {},
    secret: "",
  });
  const [error, setError] = useState<string | null>(null);
  const kind = node.kind ?? "openai";
  const cloud = kind !== "openai";
  const vertex = isVertex(kind);
  const bedrock = kind === "bedrock";
  const methods = methodsOf(useAuthMethods().data, kind);
  const method = methods.find((m) => m.method === auth.method);
  // Only the stored method's secret can be kept by leaving the field blank;
  // a newly chosen method has none stored yet.
  const secretStored =
    auth.method === node.auth &&
    (method?.secret === "api_key" ? node.has_api_key : node.has_credential);

  const save = useMutation({
    mutationFn: () =>
      client.patch(`/portal/inference/nodes/${nodeId}`, {
        // A Vertex node's address is built from its project and region.
        ...(vertex
          ? { region: region.trim(), project: project.trim() }
          : bedrock
            ? { region: region.trim() }
            : { base_url: baseUrl.trim() }),
        ...(cloud && method
          ? { ...authFields(method, auth), model_ids: parseModelIds(modelIds) }
          : {}),
        models: models
          .split(",")
          .map((m) => m.trim())
          .filter(Boolean),
        max_concurrent: Number(maxConcurrent),
        multimodal,
        enabled,
        // Absent leaves the stored key alone — the API treats an empty
        // string as "clear it", and this page never receives the key
        // back, so sending it unconditionally would wipe it on every
        // edit of anything else.
        ...(!cloud && apiKey ? { api_key: apiKey } : {}),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["inference-status"] });
      await queryClient.invalidateQueries({
        queryKey: ["inference-node", nodeId],
      });
      navigate("/inference");
    },
    onError: (err: { response?: { data?: { detail?: unknown } } }) => {
      const detail = err?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : "The node was not saved.");
    },
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    // Checked here as well as by the API, so the answer arrives before
    // the round trip rather than after it.
    if (vertex && !(region.trim() && project.trim())) {
      setError("A Vertex backend needs its project and region.");
      return;
    }
    if (bedrock && !region.trim()) {
      setError("A Bedrock backend needs its region.");
      return;
    }
    if (
      !vertex &&
      !bedrock &&
      !baseUrl.trim().startsWith("http://") &&
      !baseUrl.trim().startsWith("https://")
    ) {
      setError("The address has to start with http:// or https://");
      return;
    }
    if (!models.split(",").some((m) => m.trim())) {
      setError(
        "A node needs at least one model, or nothing will ever be routed to it.",
      );
      return;
    }
    if (cloud && !authComplete(method, auth, secretStored)) {
      setError("The chosen authentication needs every field filled in.");
      return;
    }
    if (!(Number(maxConcurrent) > 0)) {
      setError("Concurrency has to be at least 1.");
      return;
    }
    save.mutate();
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
            {KIND_LABEL[kind]}. Nothing changes until you press Save.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {error && (
        <Banner variant="critical" title="Not saved" description={error} />
      )}

      <form onSubmit={submit}>
        <Stack direction="vertical" gap="normal" className="node-form">
          {vertex ? (
            <Stack direction="horizontal" gap="normal" padding="none">
              <FormControl required>
                <FormControl.Label>Project</FormControl.Label>
                <TextInput
                  value={project}
                  onChange={(e) => setProject(e.currentTarget.value)}
                />
              </FormControl>
              <FormControl required>
                <FormControl.Label>Region</FormControl.Label>
                <TextInput
                  value={region}
                  onChange={(e) => setRegion(e.currentTarget.value)}
                />
              </FormControl>
            </Stack>
          ) : bedrock ? (
            <FormControl required>
              <FormControl.Label>Region</FormControl.Label>
              <TextInput
                value={region}
                onChange={(e) => setRegion(e.currentTarget.value)}
              />
            </FormControl>
          ) : (
            <FormControl required>
              <FormControl.Label>Address</FormControl.Label>
              <TextInput
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.currentTarget.value)}
                block
              />
              <FormControl.Caption>
                Where igni sends requests, including the <code>/v1</code> path
                if the node serves one.
              </FormControl.Caption>
            </FormControl>
          )}

          <FormControl required>
            <FormControl.Label>Models</FormControl.Label>
            <TextInput
              value={models}
              onChange={(e) => setModels(e.currentTarget.value)}
              block
            />
            <FormControl.Caption>
              Comma-separated, and every id has to match what the node answers
              to — a mistyped one does not error, it simply never matches a
              request. On a LoRA node list the adapters too.
            </FormControl.Caption>
          </FormControl>

          {cloud && (
            <FormControl>
              <FormControl.Label>Provider&apos;s names</FormControl.Label>
              <Textarea
                value={modelIds}
                onChange={(e) => setModelIds(e.currentTarget.value)}
                rows={2}
                block
              />
              <FormControl.Caption>
                One <code>name = provider-name</code> per line, where the
                provider knows a model by another name.
              </FormControl.Caption>
            </FormControl>
          )}

          <FormControl required>
            <FormControl.Label>Concurrency</FormControl.Label>
            <TextInput
              type="number"
              min={1}
              value={maxConcurrent}
              onChange={(e) => setMaxConcurrent(e.currentTarget.value)}
            />
            <FormControl.Caption>
              How many requests igni will have in flight here at once. It cannot
              see any other client of the same GPU.
            </FormControl.Caption>
          </FormControl>

          <FormControl>
            <Checkbox
              checked={multimodal}
              onChange={(e) => setMultimodal(e.currentTarget.checked)}
            />
            <FormControl.Label>Accepts images</FormControl.Label>
          </FormControl>

          <FormControl>
            <Checkbox
              checked={enabled}
              onChange={(e) => setEnabled(e.currentTarget.checked)}
            />
            <FormControl.Label>Enabled</FormControl.Label>
            <FormControl.Caption>
              Off removes it from routing entirely. For a temporary stop —
              before maintenance, say — use Stop serving on the fleet list,
              which records who did it and why.
            </FormControl.Caption>
          </FormControl>

          {cloud ? (
            <AuthFields
              methods={methods}
              value={auth}
              onChange={setAuth}
              secretStored={secretStored}
            />
          ) : (
            <FormControl>
              <FormControl.Label>API key</FormControl.Label>
              <TextInput
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.currentTarget.value)}
                placeholder={
                  node.has_api_key
                    ? "stored — leave blank to keep it"
                    : "none set"
                }
                block
              />
              <FormControl.Caption>
                Sent as a bearer token to the node. It is never returned, so
                leaving this blank keeps whatever is stored.
              </FormControl.Caption>
            </FormControl>
          )}

          <Stack direction="horizontal" gap="condensed" padding="none">
            <Button type="submit" variant="primary" disabled={save.isPending}>
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

export default EditNodePage;
