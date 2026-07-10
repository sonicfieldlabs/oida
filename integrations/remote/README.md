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
