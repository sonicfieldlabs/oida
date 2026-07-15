# Remote OÍDA

OÍDA serves a phone-oriented capture surface at `/remote` from the same
gateway as the dashboard and the embedded Akousmata navigator.

The page supports:

- future capture after a trigger;
- past capture from an on-device ring buffer;
- optional consent-scoped location;
- the active listening preset and covenant;
- storage of the uploaded sound and its listening record as an akousma.

Mobile microphone APIs require a secure browser context. OÍDA deliberately
does not configure, publish, or persist a machine-level remote-access service.
An operator who exposes this page must provide an authenticated HTTPS boundary,
keep the daemon's host/origin checks enabled, and require `OIDA_AUTH_TOKEN`
for any non-loopback bind.

The remote page is a transport surface, not a second listening implementation.
It posts to the same daemon pipeline and writes the same AKOÚŌ, Earworm, and
Akousmata contracts as every other OÍDA client.
