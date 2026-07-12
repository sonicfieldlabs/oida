# Remote Oída

Remote Oída is the existing responsive dashboard and Akousmata navigator served
by the local Oída gateway. It is not a native mobile app and it uses no cloud
backend. A phone supplies microphone, speaker, screen, touch, and browser audio
capture while all durable state and optional MOSS inference remain on the
server.

Recommended deployment: private-network Serve proxies the loopback gateway on a
dedicated HTTPS port. A dedicated port avoids breaking dashboard paths and does
not replace other routes already served by the machine.

    oida integrate remote --serve --https-port 8443

The integration records the machine's private-network DNS name as an allowed Host.
Open the reported HTTPS URL on the phone. The same origin serves /library/ and
/mcp. private-network access control remains the outer authorization boundary; Oída
stays bound to loopback.

## The remote ear (`/remote`)

v0.3 adds a phone-first capture page at `/remote` (also reported as
`remote_ear_url`). Wherever, whenever: the phone records — **past** keeps only
the last N seconds in an on-device ring buffer that overwrites itself, so a
trigger captures what was already heard; **future** records the N seconds
after the trigger — encodes WAV on the device, attaches its GPS fix when the
listener grants it, and posts to `POST /remote/listen`. The server runs the
full listening pipeline, keeps the WAV under its audio directory, writes the
akousma (the sound plus its listening file, with spec v1.2 `location` and
`capture`) into the shared store, and returns the listening event for the
remote UI. Geolocated remote listens appear on the akousmata navigator's
listening map at `/library/`.
