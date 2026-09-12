# Optional artifact delivery adapter (D6, unreleased)

Hosted/visitor intake belongs to listeningstackweb's separately scoped receipt
inbox, not to a public Oida gateway. `oida.delivery_client.DeliveryClient` accepts
only an explicitly configured loopback origin. It calls existing `/gateway/listen`
and `/operations/{id}/cancel` APIs with a local operator-selected spool path.

The adapter fixes `remember:false`, `privacy_mode:incognito`,
`raw_audio_policy:not_stored`, `ephemeral_delivery:true`, the basic route and no
song recognition. The gateway rejects contradictory retention settings. Delivery
sessions bypass recent-history/latest-event retention and completed-event
broadcast even if ordinary incognito history is enabled. The active owner
covenant still governs admission and processing. Content-free operation/event
linkage follows the existing owner journal policy; no canonical record is created.

The adapter returns only bounded operation/event linkage and outcome. It does not
export the raw report, audio path, transcript or memory contents. Publication
continues to require Akousmata's explicit selected-field grant. The relay handles
requester-scoped payload hashes, receipt expiry, consent acknowledgement, rate
limits, withdrawal and human-owned appeal intake. A visitor cannot select an
owner endpoint or automatically start execution.

A timeout or disconnection is uncertain execution, never an automatic retry.
Cancellation can fence commit while model work finishes. The relay's local
withdrawal fence discards late results independently of owner availability.
Use a dedicated local operator environment and the delivery runbook in
listeningstackweb; opening Oida itself to the network is outside this tier.
