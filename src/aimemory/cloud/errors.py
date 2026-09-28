from __future__ import annotations


class CloudError(RuntimeError):
    def __init__(self, message: str, category: str = "transient"):
        super().__init__(message)
        self.category = category


def error_category(output: str, provider: str | None = None) -> str:
    value = output.lower()
    if any(marker in value for marker in (
        "storagequotaexceeded", "insufficient storage", "not enough space",
        "storage quota", "storage full", "quota exceeded", "disk full", "drive is full",
    )):
        return "quota"
    if provider == "icloud-online":
        # A bare 401/403 (including during session renewal) can recover on the
        # next request. Only explicit Apple challenges require user action.
        if any(marker in value for marker in (
            "trust token expired", "missing icloud trust token",
            "incorrect username or password", "invalid credentials",
            "two-factor verification required", "two-factor authentication required",
            "2fa required", "requires 2fa", "invalid verification code",
            "missing pcs cookies", "cookies still missing",
            "missing x-apple-webauth-token", "termsupdateneeded",
            "timed out waiting for device approval",
            "auth session state lost", "corrupt auth session state",
        )):
            return "auth"
        if "validate2facode failed" in value and not any(marker in value for marker in (
            "timeout", "timed out", "connection", "no such host", "deadline exceeded",
        )):
            return "auth"
        return "transient"
    if any(marker in value for marker in (
        "unauthorized", "invalid_grant", "invalid credentials", "authentication failed",
        "two-factor", "2fa", "missing pcs cookies", "missing x-apple-webauth-token",
        "termsupdateneeded", "access denied", "forbidden",
    )):
        return "auth"
    return "transient"
