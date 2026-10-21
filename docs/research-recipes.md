# Bounded research recipes

Recipes compose the existing Oída reasoning queue and selected-context contracts.
They are optional application objects, not an AKOÚŌ/Earworm/MASA revision, another
memory store, a tool installer or an agent-host connection. Khipu supplies local
controls and private inspection. Source implementation and test fixtures do not
activate the installed owner or qualify model/scientific listening.

## Fixed recipes and exact admission

`selected_context_return` optionally reads up to three operator-entered public
topics and one outgoing Lexicon lead page, reads one to five explicitly selected
public/private sources, then makes a reasoning return to a retained account.
Discovery results remain leads; they never become selected context automatically.
No cursor paging, recursive expansion, generated query, dynamic tool or further
model-directed step is admitted. With private passages selected, discovery must
already have been done separately; the recipe refuses public searches/lead calls.
Exact selected public IDs can still accompany permitted private context.

`local_relisten_return` adds one explicitly selected local acoustic pass before
the reasoning return. Its input is an owner-linked listening event and expected
audio hash, chosen local model, acoustic question and focus interval. The initial
retained account is not replaced. The audio model receives the explicit question
without selected theory text. Its independent observation and selected research
can inform the later reasoning return. This is not evidence that theory improved
perception. Further model-requested acoustic actions remain disabled.

A reasoner with a bounded context (the admitted local planner) receives the observation
with the same priority as a selected reference. If it still does not fit, the recipe
stops with 409 and commits nothing; the audio call it made is counted. The audit's
`relisten_observation_shared_to_reasoner` is true only when the reasoner received it.
Before 21 October 2026 the observation ranked after every claim and was withheld from
the local planner in every recorded run, while the audit reported it as shared.

`GET /reasoning/workspace/recipes` describes the contract and defaults.
`POST /reasoning/workspace/recipes/preview` accepts the existing `Ask` shape with
`recipe: {name, capabilities, searches?, leads?, relisten?, limits}`. All routes
require the existing local-admin boundary. Preview checks current account,
comparison, provider selection and applicable local uses without queue writes,
source byte reads, retrieval, audio or inference. It returns
`oida/research-recipe/v1`, exact steps/selection and `plan_sha256`.

Dispatch uses the existing `POST /reasoning/workspace/ask`, the same selection,
an explicit stable `request_id` and `recipe_plan_sha256`. The digest includes the
current anchor, question, provider/model, source identities, comparison, recipe
and limits. It excludes the idempotency ID and absolute deadline. Changed inputs
or current anchor require a fresh preview. Current permission and source bytes
are checked again at execution; a preview never grants use. Each private inquiry
still needs fresh passage selection and, for an external/unknown reasoner, current
external disclosure plus its explicit selection flag. Automatic memory/wiki/web
expansion is refused; the comparison baseline is for inspection, without replaying
earlier dialogue into a provider.

## Bounds and stop conditions

| Bound | Default | Maximum |
| --- | --- | --- |
| Fixed stages | 5 | 5 |
| Explicit sources, public and private combined | 5 | 5 |
| Public calls, including selected-record reads | 5 | 9 |
| Text-provider calls, including one repair | 2 | 2 |
| Explicit local audio calls | 0 | 1 for the acoustic recipe |
| Queue-inclusive deadline | 120 seconds | 600 seconds |
| Requested output tokens per text-provider call | 1200 | 2200 |
| New retained turn plus its event projection | 1 MiB | 1 MiB |

For the admitted local text reasoner (`local_ecology`), a return on a real retained
account takes about 1,200–1,800 output tokens. Its answers are cut off at the 1,200
default, so set `output_tokens` to 2,200 for local recipes.

The selected operations must fit their declared capabilities and limits before
admission. A scope or extra allowance cannot invent an action. Public reads still
obey the existing 512 KiB response/25-second per-call ceiling; private selections
retain their existing source/slice/catalog bounds. Actual outbound Discovery and
provider calls, including repair and a local audio attempt, reserve a counter in
the existing owner journal before dispatch. `oida/research-recipe-execution/v1`
retains the plan digest, started stages, reservations, coverage, limits and outcome.
Reservations include uncertain outcomes; a started stage is not proof it finished.
The output-token value is a provider request cap, not a measured token count.

The byte limit is checked on the queued selection/event, selected context before
model work and the final new turn/event projection before the commit fence. It
does not cap the physical database, prior conversation or all journal versions.
There are at most five stage starts, nine public reservations, two reasoner
reservations, one audio reservation and four discovery summaries per recipe;
the existing append-only queue retains bounded copies of those receipts. Normal
owner retention/deletion policy remains responsible for accumulated storage.

Recipes stop on an unavailable/partial selected source, incomplete discovery,
withheld prompt context, current-use/covenant denial, budget/deadline, cancellation,
invalid output requiring another over-budget call, or provider fallback. They do
not commit a substitute deterministic response after the selected provider fails.
An explicitly selected local structured provider remains a valid no-LLM recipe.
Fixed sequences terminate; they cannot schedule themselves or branch indefinitely.
Public coverage and returned continuation remain inspectable even when no next
page is followed. The original pass stays retained independently.

Cancellation uses `/reasoning/workspace/jobs/{id}/cancel`, the same durable
operation and existing commit fence. Model/engine cancellation is cooperative:
a dispatched request may settle after cancellation or timeout, but late output
cannot be committed past the fence. After restart, Oída reconciles a saved turn
by its original request ID; otherwise a running job becomes interrupted. It does
not repeat a dispatched request. Inspect that same ID after an uncertain outcome;
a new operation needs fresh explicit selection and admission.

## Acoustic source boundary

Retained account projections contain no audio paths. Available acoustic choices
come only from the existing owner's event lookup and a canonical payload's
`listening_event_id` or the owner journal's `record_reference.event_id`. Callers
cannot attach arbitrary audio paths or unrelated event bodies to this recipe.
Missing source/event/hash stays unavailable. The actual current event/covenant,
file fingerprint and local engine are checked; the file is verified before and
after inference. The new recipe requires audio already bounded to at most 60
seconds and a focus interval inside it. Focus instructions do not crop the clip.
Original acoustic sidecar identities and the existing redacted provenance
projection remain intact. Transcript permission governs free-form observation
sharing. No source audio, model trace or new canonical account is exported.

## Khipu and MCP

Khipu forwards local recipe descriptions and preview under
`/api/owner/research/local/recipes` and `/recipes/preview`; preview is a read
operation, and dispatch/cancel reuse existing work/cancel admission. Its composer
uses the existing private lease for preview and selected payloads, invalidates
preview on edits, and clears drafts on hidden panels/workspace change. Owner
receipt/status reads never resubmit work. Definitive admission refusals unlock
fresh selection; transport/server uncertainty keeps the original ID for inspection.

The optional MCP functions are `oida_research_recipe_preview`,
`oida_research_recipe_start`, `oida_research_recipe_status` and
`oida_research_recipe_cancel`. Start requires a caller-chosen stable ID and exact
preview digest. This wrapper admits only public selected-context recipes with a
fresh retained-account baseline. Status returns metadata/receipt; explicit
`include_result: true` reads only that request's public-context turn, never other
conversation turns. Private-passage recipes, earlier conversations and acoustic
returns are refused at this host boundary; their current local-interface scope
does not become an external disclosure grant. Cancellation does not start work.

These source tools do not update an installed MCP plugin or connect Eco/Hermes/
another host. The existing `khipu.eco_bridge` remains separately selected trusted
host work; no keeper profile or hosted endpoint is configured by recipes. Cohort,
live model/material, human/theoretical, evaluation/training and release acceptance
remain separate phases.
