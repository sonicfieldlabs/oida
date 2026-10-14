"""Qualified counters supplied by the exact selected owner adapter."""

from oida.reasoning.contracts import ProviderRequest
from oida.reasoning.providers.base import endpoint_locality, join_url
from oida.textual_sounding import RESULT_SCHEMA


def execution_budget(registry, provider_id, model_id):
    """Never guess tokenization or infer that another provider shares framing."""
    if provider_id != "local_ecology":
        return {}
    adapter = registry.get(provider_id)
    model = next((m for m in registry.list_models(provider_id) if m.id == model_id), None)
    if model is None or "inscription" not in model.metadata.get("admitted_tasks", []):
        raise ValueError("This local deployment has no inscription admission")
    method_ref = model.metadata.get("token_counter_ref")
    if not method_ref or model.context_window != 8192 or endpoint_locality(adapter.base_url) != "local":
        raise ValueError("The local deployment has no qualified token framing")

    def count(system, user, selected_model):
        if selected_model != model_id:
            raise ValueError("Token framing belongs to another model")
        request = ProviderRequest(provider_id=provider_id, model_id=model_id,
                                  system_prompt=system, user_prompt=user,
                                  response_schema=RESULT_SCHEMA, max_output_tokens=2048)
        result = adapter._transport.request(
            "POST", join_url(adapter.base_url, "/token-budget"),
            payload=adapter._completion_payload(request),
            headers=adapter._headers(adapter._key()), timeout=60,
        ).data
        if (not isinstance(result, dict) or result.get("model_id") != model_id
            or result.get("method_ref") != method_ref or result.get("context_window") != 8192
            or result.get("output_token_reserve") != 2048
            or type(result.get("prompt_tokens")) is not int):
            raise ValueError("The tokenizer response does not identify the selected framing")
        return result["prompt_tokens"]

    return dict(token_counter=count, token_counter_ref=method_ref)
