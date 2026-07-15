from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import Mapping


class SecretStoreError(RuntimeError):
    pass


class SecretPersistenceUnavailable(SecretStoreError):
    pass


class SecretStore(ABC):
    """Credential storage boundary.

    Implementations never expose secrets through descriptors, settings, logs,
    or process arguments. Callers should keep returned values scoped to the
    provider request that needs them.
    """

    @abstractmethod
    def get(self, provider_id: str, name: str = "api_key") -> str | None:
        raise NotImplementedError

    @abstractmethod
    def set(self, provider_id: str, value: str, name: str = "api_key") -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, provider_id: str, name: str = "api_key") -> bool:
        raise NotImplementedError


class EnvironmentSecretStore(SecretStore):
    """Read-only fallback for platforms without a secure credential backend."""

    def __init__(self, environ: Mapping[str, str] | None = None, *, prefix: str = "OIDA_REASONING") -> None:
        self._environ = environ if environ is not None else os.environ
        self._prefix = prefix

    def variable_name(self, provider_id: str, name: str = "api_key") -> str:
        provider = _environment_part(provider_id)
        key = _environment_part(name)
        return f"{self._prefix}_{provider}_{key}"

    def get(self, provider_id: str, name: str = "api_key") -> str | None:
        value = self._environ.get(self.variable_name(provider_id, name))
        return value if value else None

    def set(self, provider_id: str, value: str, name: str = "api_key") -> None:
        del provider_id, value, name
        raise SecretPersistenceUnavailable(
            "no secure credential backend is available; set the documented environment variable instead"
        )

    def delete(self, provider_id: str, name: str = "api_key") -> bool:
        del provider_id, name
        return False


class MacOSKeychainSecretStore(SecretStore):
    """Store credentials in the user's login Keychain through ``security``."""

    def __init__(
        self,
        *,
        executable: str = "/usr/bin/security",
        service: str = "dev.sonicfieldlabs.oida.reasoning",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not executable:
            raise SecretPersistenceUnavailable("macOS Keychain command is unavailable")
        self.executable = executable
        self.service = service
        self.timeout_seconds = timeout_seconds

    def get(self, provider_id: str, name: str = "api_key") -> str | None:
        result = self._run(
            [
                self.executable,
                "find-generic-password",
                "-a",
                _account(provider_id, name),
                "-s",
                self.service,
                "-w",
            ]
        )
        if result.returncode != 0:
            return None
        value = result.stdout.rstrip("\r\n")
        return value or None

    def set(self, provider_id: str, value: str, name: str = "api_key") -> None:
        if not value:
            raise ValueError("secret value cannot be empty")
        # `security` documents a final `-w` as the prompt form. Supplying the
        # answer on stdin keeps the secret out of argv and process listings.
        result = self._run(
            [
                self.executable,
                "add-generic-password",
                "-U",
                "-a",
                _account(provider_id, name),
                "-s",
                self.service,
                "-w",
            ],
            input_text=value + "\n",
        )
        if result.returncode != 0:
            raise SecretStoreError("macOS Keychain rejected the credential update")

    def delete(self, provider_id: str, name: str = "api_key") -> bool:
        result = self._run(
            [
                self.executable,
                "delete-generic-password",
                "-a",
                _account(provider_id, name),
                "-s",
                self.service,
            ]
        )
        return result.returncode == 0

    def _run(self, argv: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                argv,
                input=input_text,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SecretStoreError("macOS Keychain command failed") from exc


class KeyringSecretStore(SecretStore):
    """Optional cross-platform adapter for an installed system keyring."""

    def __init__(self, *, service: str = "dev.sonicfieldlabs.oida.reasoning") -> None:
        try:
            import keyring
        except ImportError as exc:  # pragma: no cover - depends on optional host package
            raise SecretPersistenceUnavailable("the optional keyring package is not installed") from exc
        self._keyring = keyring
        self.service = service

    def get(self, provider_id: str, name: str = "api_key") -> str | None:
        return self._keyring.get_password(self.service, _account(provider_id, name))

    def set(self, provider_id: str, value: str, name: str = "api_key") -> None:
        if not value:
            raise ValueError("secret value cannot be empty")
        self._keyring.set_password(self.service, _account(provider_id, name), value)

    def delete(self, provider_id: str, name: str = "api_key") -> bool:
        try:
            self._keyring.delete_password(self.service, _account(provider_id, name))
        except Exception:
            return False
        return True


class LayeredSecretStore(SecretStore):
    """Let environment variables override a writable secure backend."""

    def __init__(self, environment: EnvironmentSecretStore, writable: SecretStore) -> None:
        self.environment = environment
        self.writable = writable

    def get(self, provider_id: str, name: str = "api_key") -> str | None:
        return self.environment.get(provider_id, name) or self.writable.get(provider_id, name)

    def set(self, provider_id: str, value: str, name: str = "api_key") -> None:
        self.writable.set(provider_id, value, name)

    def delete(self, provider_id: str, name: str = "api_key") -> bool:
        return self.writable.delete(provider_id, name)


def default_secret_store() -> SecretStore:
    environment = EnvironmentSecretStore()
    if sys.platform == "darwin":
        executable = shutil.which("security") or ("/usr/bin/security" if os.path.exists("/usr/bin/security") else None)
        if executable:
            return LayeredSecretStore(environment, MacOSKeychainSecretStore(executable=executable))
    try:
        return LayeredSecretStore(environment, KeyringSecretStore())
    except SecretPersistenceUnavailable:
        return environment


def _account(provider_id: str, name: str) -> str:
    return f"{_identifier(provider_id)}:{_identifier(name)}"


def _identifier(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not re.fullmatch(r"[A-Za-z0-9._-]+", normalized):
        raise ValueError("secret provider/name must use letters, numbers, dot, underscore, or hyphen")
    return normalized


def _environment_part(value: str) -> str:
    return _identifier(value).replace(".", "_").replace("-", "_").upper()
