# The models

For the customer's security, risk and procurement teams. Where igni's
models come from, under what licence, what runs where, and what to check at
hand-over. Part of the security pack ([SECURITY_PACK.md](SECURITY_PACK.md)).

Ignite Ember is the company. igni is the software.

## 1. Which models

| Model | Base model | How it was made | Status |
|---|---|---|---|
| igni 1.0 4B | Qwen3.5-4B, open weights, bf16 | LoRA fine-tuning in two stages, supervised (SFT) then preference (DPO), both merged into the base | Shipped. Model card: `igni__fine-tuning/outputs/igni-1.0-4b/MODEL_CARD.md` |
| igni 1.0 27B | Qwen3.8-27B, open weights | Not trained yet. The training code is pinned to this base (`BASE_MODEL` in `igni__fine-tuning/.env.example`) | No trained weights and no model card yet |
| Domain models the Factory builds | A base the customer chooses with Ignite Ember | Tuned inside the customer's network, on the customer's data, by the customer's expert | Each one is the customer's |

Each igni 1.0 model is offered in two forms: one whole model with the
adapters merged in, which needs nothing else at serve time, and the LoRA
adapters for each training stage, so the result can be rebuilt from the base
and checked.

The base models come from Alibaba's Qwen team and are published as open
weights under the Apache-2.0 licence. Apache-2.0 allows commercial use, modification and redistribution, and
asks that the licence and notices travel with the weights. Ignite Ember has
not yet released a tuned model on a European base.

What igni 1.0 was trained to do, from its model card: follow igni's tool and
agent conventions, and resist prompt injection. Output from a tool is
wrapped in a marked, nonce-tagged block at training and at serve time, so
the model learns to treat it as data rather than instructions. The 4B model
scored 305 of 343 on Ignite Ember's held-out scenario set
(`category_eval.json`).

Where the training corpora came from: `__ORGANISATION_TO_COMPLETE__`. No
customer's data is in them: Ignite Ember never receives any.

## 2. What runs where

| | Where | What it connects to |
|---|---|---|
| The model weights | Files on the customer's GPU hosts | Nothing. Weights are data, not a program |
| The inference server | vLLM, run by the customer on its own GPUs, on premises or rented | Whatever the customer allows (section 3) |
| igni server | The customer's servers | Each inference node's `base_url`, over the OpenAI-compatible API ([gpu-clusters.md](gpu-clusters.md)) |
| The client's decision model, Laya (`convaiinnovations/laya`, Apache-2.0) | Developer machines, when the decisions extra is installed | Hugging Face on first use, unless `air_gapped: true`, which loads it only from a pre-seeded local cache (`igni/src/ember_code/decisions_server/model.py`) |

igni server does not download, sign or check model weights. Nothing in it
checks a licence before routing to a node, and the weights carry no licence
key and no expiry ([EXIT_TEST_RUNBOOK.md](EXIT_TEST_RUNBOOK.md), 4.5). The
same files can be served by any software that reads safetensors.

## 3. Keeping the inference server inside the network

igni does not package or configure vLLM, so its egress tests do not cover
it. Two vLLM defaults reach the internet, according to vLLM's own
documentation:

* **Usage statistics.** vLLM sends anonymous usage data to its developers
  unless told not to. Set `VLLM_NO_USAGE_STATS=1` or `DO_NOT_TRACK=1` on
  every node.
* **Model download.** Given a Hugging Face model id rather than a path,
  vLLM downloads the weights. Serve from a local path, and set
  `HF_HUB_OFFLINE=1` so a mistake fails rather than fetching.

Check both on a node with its egress blocked before production, and keep
the result with the exit-test evidence
([EXIT_TEST_RUNBOOK.md](EXIT_TEST_RUNBOOK.md), step B1).

## 4. What to check at hand-over

* **The file list and checksums.** The merged model (configuration,
  `model.safetensors`, tokenizer, chat template, generation configuration)
  and each adapter, with a SHA-256 for every file.
* **The upstream licence.** The base model's Apache-2.0 licence and notice
  files, kept with the weights.
* **For a Factory model:** the merged weights, the adapters, the dataset and
  the frozen evaluation set, with checksums. No code packages this hand-over
  yet ([EXIT_TEST_RUNBOOK.md](EXIT_TEST_RUNBOOK.md), G9).

## 5. Gaps

* **Model files are not signed.** The release bundle signs igni's images,
  chart and SBOMs, not model weights ([OFFLINE_UPDATES.md](OFFLINE_UPDATES.md)).
  Integrity at hand-over rests on checksums sent through a second channel.
* **The 27B model has no trained weights yet**, and so no model card or checksums.
* **The training-data statement** in section 1 is not filled in.
* **The base is not European.** A customer whose policy rules out a model
  from a given origin should know the base is Qwen. The weights are open and
  run with no outbound connection, so they can be inspected and run fully
  inside the network, but their origin does not change.
