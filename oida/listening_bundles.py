"""Cached-pass packaging delegates disclosure and imports to Akousmata."""

from akousmata_app.bundles import export_bundle, import_bundle


def package_passes(store, selections, *, disclosure="private"):
    if not isinstance(selections, list) or not 1 <= len(selections) <= 128:
        raise ValueError("Select 1–128 retained passes")
    ids = []
    for selected in selections:
        if not isinstance(selected, dict) or set(selected) != {
            "record_ref",
            "listening_ref",
        }:
            raise ValueError("Each pass requires record_ref and listening_ref")
        record = store.get(selected["record_ref"])
        if record is None or not any(
            p.get("listening_id") == selected["listening_ref"]
            for p in record.get("auditum", {}).get("listenings", [])
        ):
            raise ValueError("Cached pass does not resolve to a retained account")
        ids.append(record["akousma_id"])
    result = export_bundle(store, list(dict.fromkeys(ids)), disclosure=disclosure)
    # Owner response only: never widen a public projection with private pass IDs.
    return {
        **result,
        "cached_passes": selections,
        "execution": "not_requested",
        "projection_owner": "akousmata",
    }


def receive(store, data, *, supported_contracts):
    return import_bundle(store, data, supported_contracts=supported_contracts)
