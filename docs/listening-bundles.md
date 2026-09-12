# Cached passes and Cosmoaudition subscription (D5, unreleased)

`POST /akousmata/bundles/export` accepts `selections` of retained
`record_ref`/`listening_ref` pairs and `disclosure` (`private` or
`public-projection`). It verifies actual auditum pass identities and delegates
packaging to Akousmata. Selected cached-pass IDs are returned to the owner, not
added to the public archive. No new pass is invented and no second sanitizer is
introduced. `/akousmata/bundles/import` delegates the bounded `archive_base64`
and `supported_contracts` request to the same Akousmata verified import service.
The embedded `/library/api/bundles/*` owner routes remain available too.

`POST /sources/cosmoaudition/poll` performs one explicit owner-requested poll.
Configure `OIDA_COSMOAUDITION_URL` as a loopback HTTP origin with an explicit port.
The body supplies `mode` (fixture/live), `observation_ref`, `consent_ref`,
`remember` and a stable `operation_id`. This invocation declares permission for
that selected observation; the existing owner preflight may still refuse it.
The fixed `/api/observation-feed` endpoint must preserve `relation.of: signal`,
the non-acoustic source register and original acquisition mode. Redirects,
remote origins and responses exceeding 2 MiB are refused.

The existing O6 observation operation owns validation, optional retention,
retry/cancellation and the final policy check. A repeated completed operation ID
does not fetch a new snapshot. New work needs a new ID. This is bounded polling,
not a persistent stream subscriber or a public Cosmo gateway. Provider process
IDs and receipt attribution are retained; fixture evidence never becomes live.
The shared Earworm offline MASA adapter replaces the former local validator copy.

Imports preserve canonical identity, extensions and current forgetting rules.
Original audio locators remain original locators; record import does not promise
local availability of external media or import public grants.

For observation subscription, prefer `signal_id` (for example `carbon_intensity_actual`) over a prior `observation_ref`: each new Cosmo snapshot has fresh observation IDs. Oída resolves the named field within the fetched snapshot and passes that exact ID to O6. Zero or multiple matches refuse. Completed operation IDs return the existing 409 receipt without another fetch.
