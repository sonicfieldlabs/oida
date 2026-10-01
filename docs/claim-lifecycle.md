# Receiving-claim expiry (unreleased)

Observation intake accepts optional `claim_validity` and `claim_retention` objects
using Earworm's existing listening-context claim fields. The declarations bind to
each receiving claim; they do not rewrite the preserved provider snapshot or infer
validity from provider health. Missing declarations retain the contract's explicit
unknown state. A new intake with expired, future-issued or malformed declared
claims is refused before writing a canonical record.

For example, validity can be `{ "status": "expires", "issued_at":
"2026-09-01T00:00:00Z", "expires_at": "2026-09-15T00:00:00Z" }`. Independent
retention review can be `{ "status": "review_after", "review_after":
"2026-10-01T00:00:00Z", "policy_ref": "owner-review-policy" }`.

`GET /owner/records/{id}` returns the preserved canonical record together with
`claim_evaluation`, evaluated at the current read time. Historical claim text stays
historical: `expired`, `not_yet_valid`, `unknown` and `current` remain distinct.
Retention review is reported separately and never silently extends validity or
creates a deletion/forgetting event. The owner journal's replayed record references
remain references; use the owner record endpoint to inspect current claim status.

Akousmata's current export/publication gate refuses records whose declared receiving
claims are not all current. The public journal therefore invalidates its old epoch
when a formerly permitted record expires, even if no source bytes changed. A fresh
export also rechecks current validity. These operations do not mutate canonical
records or automatically delete earlier local export archives. Already-downloaded
copies cannot be recalled, and historic packs must not be described as current.

This implements declared currency, not claim truth or automatic retention policy.
An application must supply a defensible validity interval; neither a successful
transport nor a fresh request can renew an expired claim.
