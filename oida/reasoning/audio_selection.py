"""Request-scoped selection of registered audio deployments; no credentials in selectors."""

from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from oida.reasoning.model_catalog import MODEL_SPECS


class AudioModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    model_id: str = Field(min_length=1, max_length=256)
    deployment_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


def selector(spec):
    return AudioModel(
        provider_id=spec.provider_id,
        model_id=spec.id,
        deployment_id="audio-"
        + sha256(f"{spec.provider_id}/{spec.id}".encode()).hexdigest()[:24],
    )


def resolve(selection):
    for spec in MODEL_SPECS:
        if spec.selectable and selector(spec) == selection:
            return spec
    raise ValueError("Unknown registered audio deployment")


_selection = ContextVar("oida_audio_selection", default=None)


def selected_audio_model():
    return _selection.get()


@contextmanager
def use_audio_model(selection):
    if selection is not None:
        resolve(selection)
    token = _selection.set(selection)
    try:
        yield
    finally:
        _selection.reset(token)


def validate_endpoint(provider_id, base_url):
    url = urlsplit(base_url or "")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError(
            "Audio endpoint must not contain credentials, query or fragment"
        )
    host = url.hostname or ""
    if provider_id == "google":
        valid = (
            url.scheme == "https"
            and host == "generativelanguage.googleapis.com"
            and url.port in (None, 443)
            and url.path.rstrip("/") == "/v1beta"
        )
    elif provider_id == "alibaba":
        valid = (
            url.scheme == "https"
            and url.port in (None, 443)
            and url.path.rstrip("/") == "/compatible-mode/v1"
            and (
                host == "dashscope-intl.aliyuncs.com"
                or host.endswith(".ap-southeast-1.maas.aliyuncs.com")
            )
        )
    elif provider_id == "local_audio":
        valid = url.scheme in {"http", "https"} and host in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
    elif provider_id == "stepfun":
        valid = (
            url.scheme == "https"
            and host == "api.stepfun.com"
            and url.port in (None, 443)
            and url.path.rstrip("/") == "/v1"
        )
    elif provider_id == "groq":
        valid = (
            url.scheme == "https"
            and host == "api.groq.com"
            and url.port in (None, 443)
            and url.path.rstrip("/") == "/openai/v1"
        )
    else:
        valid = False
    if not valid:
        raise ValueError("Selected audio deployment has an unsupported endpoint")
