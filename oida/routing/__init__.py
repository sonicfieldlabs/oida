"""Decision routing: typed, bounded next-action judgments for the coordinator.

Stage layout (model-routing plan §3/§6):

- ``contracts`` — the versioned settings/context/question/proposal shapes.
- ``providers`` — one adapter per decision provider; none may answer with an
  open-ended text field, and providers that do not produce probabilities keep
  those fields absent instead of fabricating Jev-like scores.
- ``registry`` — provider resolution over Oída's own reasoning registry, plus
  the always-available deterministic rules provider.
- ``service`` — idempotent, journaled ``/routing/{options,config,decide}``
  routes. Decisions are proposals only: execution authority stays with the
  existing host admission path.
"""

from oida.routing.service import routing_router  # noqa: F401
