from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from aimemory.cloud.google_drive import rclone_binary
from aimemory.cloud.providers import provider_label
from aimemory.state import now, read_json, write_json
from aimemory.cloud.destination import remote_path
from aimemory.cloud.errors import CloudError, error_category


ICLOUD_WEB_APPROVAL_MESSAGE = (
    "Le code 2FA a été accepté. La Protection avancée des données bloque encore iCloud Drive : "
    "sur l'iPhone, ouvrez Réglages > compte Apple > iCloud et activez Accès aux données iCloud "
    "sur le Web, puis approuvez la demande Apple éventuelle et cliquez sur Réessayer iCloud."
)

ICLOUD_TERMS_MESSAGE = (
    "Le code 2FA a été accepté, mais Apple n'a fourni qu'une session iCloud partielle. "
    "Ouvrez icloud.com, connectez-vous et acceptez les nouvelles conditions iCloud éventuelles. "
    "Revenez ensuite dans AI Memory et cliquez sur J'ai accepté, relancer."
)


class ICloudWebApprovalRequired(RuntimeError):
    pass


class ICloudTermsAcceptanceRequired(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RcloneProviderSpec:
    provider: str
    remote_name: str
    backend: str

    @property
    def remote(self) -> str:
        return f"{self.remote_name}:AI-Memory"


RCLONE_PROVIDER_SPECS = {
    "dropbox-online": RcloneProviderSpec("dropbox-online", "aimemory-dropbox", "dropbox"),
    "onedrive-online": RcloneProviderSpec("onedrive-online", "aimemory-onedrive", "onedrive"),
    "icloud-online": RcloneProviderSpec("icloud-online", "aimemory-icloud", "iclouddrive"),
}


class RcloneCloudProvider:
    def __init__(self, paths, spec: RcloneProviderSpec):
        self.paths = paths
        self.spec = spec
        self.config = paths.home / "credentials" / "rclone.conf"

    def target_root(self) -> str:
        current = read_json(self.paths.state / "cloud.json")
        return current.get("root", "AI-Memory") if current.get("provider") == self.spec.provider else "AI-Memory"

    def target_remote(self) -> str:
        return remote_path({"provider": self.spec.provider, "root": self.target_root()})

    @classmethod
    def for_provider(cls, paths, provider: str) -> "RcloneCloudProvider":
        if provider not in RCLONE_PROVIDER_SPECS:
            raise ValueError("Destination cloud inconnue.")
        return cls(paths, RCLONE_PROVIDER_SPECS[provider])

    def run(self, args: list[str], timeout: int = 600, config: Path | None = None, stdin=None) -> str:
        try:
            result = subprocess.run(
                [
                    rclone_binary(),
                    "--config",
                    str(config or self.config),
                    "--cache-dir",
                    str(self.paths.cache / "rclone"),
                    "--contimeout",
                    "15s",
                    "--timeout",
                    "60s",
                    "--retries",
                    "2",
                    *args,
                ],
                stdin=stdin if stdin is not None else subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            raise CloudError(f"{provider_label(self.spec.provider)} n'a pas répondu. Réessayez.") from None
        if result.returncode:
            if self.spec.provider == "icloud-online":
                if _icloud_terms_error(result.stderr):
                    raise ICloudTermsAcceptanceRequired(ICLOUD_TERMS_MESSAGE)
                if _icloud_web_approval_error(result.stderr):
                    raise ICloudWebApprovalRequired(ICLOUD_WEB_APPROVAL_MESSAGE)
            raise CloudError(_friendly_rclone_error(self.spec.provider, result.stderr, result.returncode), error_category(result.stderr))
        return result.stdout

    def connect(self, options: dict | None = None) -> dict | None:
        options = options or {}
        if self.spec.provider == "icloud-online":
            if options.get("restart_after_terms"):
                return self._restart_icloud_after_terms()
            if options.get("resume_after_approval"):
                return self._finish_icloud_connect()
            code = str(options.get("two_factor_code") or "").strip()
            if code:
                return self._continue_icloud_connect(code)
            return self._begin_icloud_connect(options)

        self.config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.parent.chmod(0o700)
        pending = self.config.with_suffix(f".{self.spec.provider}.pending")
        auth_path = self.paths.state / f"oauth-{self.spec.provider}.json"
        continuing = options.get("selection") is not None
        if not continuing and self.config.exists():
            shutil.copy2(self.config, pending)
        try:
            if continuing:
                auth = read_json(auth_path)
                selection = str(options["selection"])
                if not pending.exists() or selection not in [item["value"] for item in auth.get("choices", [])]:
                    raise ValueError("Sélection de Drive invalide ou session expirée.")
                args = ["config", "update", self.spec.remote_name, "--continue", "--state", auth["state"],
                        "--result", selection, "--non-interactive"]
            else:
                auth_path.unlink(missing_ok=True)
                try:
                    self.run(["config", "delete", self.spec.remote_name], timeout=30, config=pending)
                except RuntimeError:
                    pass
                args = ["config", "create", self.spec.remote_name, self.spec.backend, "config_is_local", "true"]
                if self.spec.provider == "onedrive-online":
                    args.extend(["region", "global", "drive_type", options.get("onedrive_type") or "personal",
                                 "config_type", "onedrive"])
                args.append("--non-interactive")
            result = _config_result(self.run(args, timeout=300, config=pending))
            # rclone may ask for a drive when one Microsoft account exposes several.
            for _ in range(4):
                if not result.get("State"):
                    break
                option = result.get("Option") or {}
                if option.get("Name") == "config_driveok":
                    result = _config_result(self.run(["config", "update", self.spec.remote_name, "--continue",
                        "--state", result["State"], "--result", "true", "--non-interactive"], config=pending))
                    continue
                choices = [{"value": str(item["Value"]), "label": str(item.get("Help") or item["Value"])}
                           for item in option.get("Examples", []) if "Value" in item]
                if not choices:
                    raise RuntimeError("Le fournisseur demande une étape non prise en charge. Aucun compte n'a été remplacé.")
                auth = {"status": "needs_selection", "provider": self.spec.provider, "state": result["State"],
                        "choices": choices, "message": "Choisissez le Drive de sauvegarde."}
                pending.chmod(0o600)
                write_json(auth_path, auth)
                return auth
            if result.get("State"):
                raise RuntimeError("La configuration du Drive n'est pas terminée.")
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(pending)
            if not parser.has_section(self.spec.remote_name):
                raise RuntimeError(f"Connexion {provider_label(self.spec.provider)} incomplète.")
            if self.spec.provider != "icloud-online" and not parser.has_option(self.spec.remote_name, "token"):
                raise RuntimeError(f"{provider_label(self.spec.provider)} n'a pas renvoyé de jeton d'accès.")
            pending.chmod(0o600)
            self.run(["mkdir", self.target_remote()], config=pending)
            os.replace(pending, self.config)
            write_json(
                self.paths.state / "cloud.json",
                {
                    "provider": self.spec.provider,
                    "root": self.target_root(),
                    "remote": self.spec.remote_name,
                    "mode": "online",
                    "connection_id": uuid.uuid4().hex,
                },
            )
            write_json(self.paths.state / "sync-status.json", {"status": "connected"})
            auth_path.unlink(missing_ok=True)
        finally:
            if not auth_path.exists():
                pending.unlink(missing_ok=True)

    @property
    def icloud_pending_config(self) -> Path:
        return self.config.with_suffix(".icloud-pending")

    @property
    def icloud_auth_state_path(self) -> Path:
        return self.paths.state / "icloud-auth.json"

    def pending_icloud_auth(self) -> dict:
        state = read_json(self.icloud_auth_state_path)
        if not state or not self.icloud_pending_config.is_file():
            return {}
        return state

    def cancel_pending_icloud_auth(self) -> None:
        self.icloud_pending_config.unlink(missing_ok=True)
        self.icloud_auth_state_path.unlink(missing_ok=True)

    def reset_icloud(self) -> None:
        self.cancel_pending_icloud_auth()
        shutil.rmtree(self.paths.cache / "rclone", ignore_errors=True)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.config)
        if parser.remove_section(self.spec.remote_name):
            with self.config.open("w", encoding="utf-8") as stream:
                parser.write(stream)
            self.config.chmod(0o600)
        if read_json(self.paths.state / "cloud.json").get("provider") == "icloud-online":
            (self.paths.state / "cloud.json").unlink(missing_ok=True)
            write_json(self.paths.state / "sync-status.json", {"status": "disconnected"})

    def _icloud_session_is_trusted(self) -> bool:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.icloud_pending_config)
        section = self.spec.remote_name
        return (
            parser.has_section(section)
            and bool(parser.get(section, "trust_token", fallback=""))
            and not parser.get(section, "_auth_session", fallback="")
        )

    def _set_icloud_web_approval(self, previous: dict | None = None) -> dict:
        state = {
            "status": "needs_web_approval",
            "started_at": (previous or {}).get("started_at") or now(),
            "message": ICLOUD_WEB_APPROVAL_MESSAGE,
        }
        write_json(self.icloud_auth_state_path, state)
        return state

    def _set_icloud_terms_acceptance(self, previous: dict | None = None) -> dict:
        state = {
            "status": "needs_terms_acceptance",
            "started_at": (previous or {}).get("started_at") or now(),
            "message": ICLOUD_TERMS_MESSAGE,
        }
        write_json(self.icloud_auth_state_path, state)
        return state

    def _begin_icloud_connect(self, options: dict) -> dict:
        apple_id = str(options.get("apple_id") or "").strip()
        password = str(options.get("password") or "")
        if not apple_id or not password:
            raise ValueError("Renseignez l'Apple ID et le mot de passe iCloud.")

        return self._start_icloud_connect(apple_id, password)

    def _restart_icloud_after_terms(self) -> dict:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.icloud_pending_config)
        section = self.spec.remote_name
        apple_id = parser.get(section, "apple_id", fallback="").strip()
        password = parser.get(section, "password", fallback="")
        if not apple_id or not password:
            self.cancel_pending_icloud_auth()
            raise RuntimeError(
                "La session Apple ne contient plus les identifiants chiffrés. "
                "Reconnectez iCloud avec l'Apple ID et le mot de passe."
            )
        return self._start_icloud_connect(apple_id, password, password_is_obscured=True)

    def _start_icloud_connect(
        self,
        apple_id: str,
        password: str,
        *,
        password_is_obscured: bool = False,
    ) -> dict:

        self.config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.parent.chmod(0o700)
        self.cancel_pending_icloud_auth()
        pending = self.icloud_pending_config
        if self.config.exists():
            shutil.copy2(self.config, pending)
        else:
            pending.touch(mode=0o600)
        pending.chmod(0o600)
        try:
            try:
                self.run(["config", "delete", self.spec.remote_name], timeout=30, config=pending)
            except RuntimeError:
                pass
            obscured = (
                password
                if password_is_obscured
                else self.run(["obscure", password], timeout=30, config=pending).strip()
            )
            output = self.run(
                [
                    "config",
                    "create",
                    self.spec.remote_name,
                    self.spec.backend,
                    "service",
                    "drive",
                    "apple_id",
                    apple_id,
                    "password",
                    obscured,
                    "--no-obscure",
                    "--non-interactive",
                ],
                timeout=300,
                config=pending,
            )
            result = _config_result(output)
            if not result.get("State"):
                return self._finish_icloud_connect()
            if result.get("Option", {}).get("Name") != "config_2fa":
                raise RuntimeError("iCloud a demandé une étape d'authentification non prise en charge.")
            auth = {
                "status": "needs_2fa",
                "state": result["State"],
                "started_at": now(),
                "message": (
                    "Code Apple demandé. Validez la notification sur votre appareil, "
                    "puis saisissez ce code sans relancer la connexion."
                ),
            }
            write_json(self.icloud_auth_state_path, auth)
            return auth
        except Exception:
            if not self.icloud_auth_state_path.exists():
                self.cancel_pending_icloud_auth()
            raise

    def _continue_icloud_connect(self, code: str) -> dict:
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("Le code Apple doit contenir exactement 6 chiffres.")
        auth = self.pending_icloud_auth()
        if auth.get("status") != "needs_2fa":
            raise ValueError("Aucun code Apple n'est attendu. Recommencez la connexion si nécessaire.")
        state = str(auth.get("state") or "")
        if not state:
            self.cancel_pending_icloud_auth()
            raise RuntimeError("La session Apple a expiré. Recommencez avec l'Apple ID et le mot de passe.")
        try:
            output = self.run(
                [
                    "config",
                    "update",
                    self.spec.remote_name,
                    "--continue",
                    "--state",
                    state,
                    "--result",
                    code,
                    "--non-interactive",
                ],
                timeout=300,
                config=self.icloud_pending_config,
            )
        except ICloudTermsAcceptanceRequired:
            if self._icloud_session_is_trusted():
                return self._set_icloud_terms_acceptance(auth)
            raise
        except ICloudWebApprovalRequired:
            if self._icloud_session_is_trusted():
                return self._set_icloud_web_approval(auth)
            raise
        result = _config_result(output)
        if result.get("State"):
            auth.update(state=result["State"], message=result.get("Option", {}).get("Help") or auth["message"])
            write_json(self.icloud_auth_state_path, auth)
            return auth
        return self._finish_icloud_connect()

    def _finish_icloud_connect(self) -> dict:
        pending = self.icloud_pending_config
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(pending)
        section = self.spec.remote_name
        if not self._icloud_session_is_trusted():
            raise RuntimeError("iCloud n'a pas renvoyé de session de confiance.")
        write_json(self.icloud_auth_state_path, {
            "status": "checking_access",
            "message": "Code validé. Vérification de l'accès à iCloud Drive ; Apple peut demander une autorisation supplémentaire sur votre appareil.",
        })
        pending.chmod(0o600)
        try:
            self.run(["mkdir", self.target_remote()], config=pending)
        except ICloudTermsAcceptanceRequired:
            return self._set_icloud_terms_acceptance(read_json(self.icloud_auth_state_path))
        except ICloudWebApprovalRequired:
            return self._set_icloud_web_approval(read_json(self.icloud_auth_state_path))
        except Exception as exc:
            auth_error = getattr(exc, "category", None) == "auth"
            state = {
                "status": "access_failed" if auth_error else "needs_access_retry",
                "message": "La session Apple a expiré. Reconnectez ce compte." if auth_error else
                    "Session Apple validée, mais accès au Drive temporairement indisponible. Réessayez sans saisir un nouveau code.",
            }
            write_json(self.icloud_auth_state_path, state)
            if auth_error:
                raise
            return state
        os.replace(pending, self.config)
        self.icloud_auth_state_path.unlink(missing_ok=True)
        write_json(
            self.paths.state / "cloud.json",
            {
                "provider": self.spec.provider,
                "root": self.target_root(),
                "remote": self.spec.remote_name,
                "mode": "online",
                "connection_id": uuid.uuid4().hex,
            },
        )
        write_json(self.paths.state / "sync-status.json", {"status": "connected"})
        return {"status": "connected", "message": "iCloud Drive est connecté."}

    def exchange(self, local: Path, progress) -> None:
        for source, destination, phase in ((str(local), self.spec.remote, "uploading"), (self.spec.remote, str(local), "downloading")):
            progress(phase, bytes=0, totalBytes=0, speed=0)
            args = [
                rclone_binary(),
                "--config",
                str(self.config),
                "copy",
                source,
                destination,
                "--ignore-existing",
                "--exclude",
                ".tmp-*",
                "--use-json-log",
                "--stats",
                "2s",
                "--stats-log-level",
                "NOTICE",
                "--contimeout",
                "15s",
                "--timeout",
                "60s",
                "--retries",
                "2",
            ]
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            started = time.monotonic()
            try:
                while True:
                    try:
                        _, output = process.communicate(timeout=2)
                        done = True
                    except subprocess.TimeoutExpired as exc:
                        output = exc.stderr or b""
                        done = False
                    stats = {}
                    for line in output.splitlines()[-12:]:
                        try:
                            record = json.loads(line)
                            if "stats" in record:
                                stats = record["stats"]
                        except (ValueError, UnicodeDecodeError):
                            pass
                    progress(phase, **{key: stats.get(key, 0) for key in ("bytes", "totalBytes", "speed", "transfers", "totalTransfers")})
                    if done:
                        break
                    if time.monotonic() - started > 3600:
                        raise TimeoutError(f"Transfert {provider_label(self.spec.provider)} trop long. Relancez la synchronisation.")
                if process.returncode:
                    raise RuntimeError(_friendly_rclone_error(self.spec.provider, output, process.returncode))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def disconnect(self) -> None:
        self.cancel_pending_icloud_auth()
        try:
            self.run(["config", "delete", self.spec.remote_name], timeout=30)
        except RuntimeError:
            pass
        (self.paths.state / "cloud.json").unlink(missing_ok=True)


def _config_result(output: str) -> dict:
    try:
        result = json.loads(output or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Réponse d'authentification iCloud illisible.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Réponse d'authentification iCloud invalide.")
    if result.get("Error"):
        raise RuntimeError("Le fournisseur a refusé cette étape. Vérifiez la saisie ou recommencez la connexion.")
    return result


def _icloud_web_approval_error(output: str | bytes | None) -> bool:
    text = (output or "").decode("utf-8", "ignore") if isinstance(output, bytes) else (output or "")
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("missing pcs cookies", "requestpcs(")
    )


def _icloud_terms_error(output: str | bytes | None) -> bool:
    text = (output or "").decode("utf-8", "ignore") if isinstance(output, bytes) else (output or "")
    lowered = text.lower()
    return "missing x-apple-webauth-token" in lowered or "termsupdateneeded" in lowered


def _friendly_rclone_error(provider: str, output: str | bytes | None, code: int) -> str:
    text = (output or "").decode("utf-8", "ignore") if isinstance(output, bytes) else (output or "")
    lowered = text.lower()
    name = provider_label(provider)
    if provider == "icloud-online":
        if _icloud_terms_error(text):
            return ICLOUD_TERMS_MESSAGE
        if _icloud_web_approval_error(text):
            return ICLOUD_WEB_APPROVAL_MESSAGE
        if any(marker in lowered for marker in ("2fa", "two-factor", "verification", "mfa", "auth", "unauthorized", "forbidden")):
            return (
                "La vérification Apple a échoué ou la session a expiré. "
                "Si un code est attendu, vérifiez les six chiffres. Sinon, recommencez la connexion iCloud. "
                "Utilisez le mot de passe Apple ID normal, pas un mot de passe spécifique d'app."
            )
    if any(marker in lowered for marker in ("insufficient storage", "not enough space", "quota", "storage full", "drive is full")):
        return f"{name} est plein. Libérez de l'espace ou changez de destination cloud."
    if any(marker in lowered for marker in ("unauthorized", "invalid_grant", "access denied", "forbidden", "authentication", "auth")):
        return f"Connexion {name} refusée ou expirée. Reconnectez ce compte."
    if provider == "icloud-online":
        return (
            "iCloud Drive est temporairement indisponible. Vérifiez le réseau ; "
            "les transferts validés sont conservés et une nouvelle tentative est prévue."
        )
    return f"{name} a échoué (code {code}). Vérifiez la connexion puis relancez."
