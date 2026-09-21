from __future__ import annotations


class CloudError(RuntimeError):
    def __init__(self, message: str, category: str = "transient"):
        super().__init__(message)
        self.category = category


def error_category(output: str) -> str:
    value = output.lower()
    if any(marker in value for marker in (
        "storagequotaexceeded", "insufficient storage", "not enough space",
        "storage quota", "storage full", "quota exceeded", "disk full",
    )):
        return "quota"
    if any(marker in value for marker in (
        "unauthorized", "invalid_grant", "invalid credentials", "authentication failed",
        "two-factor", "2fa", "missing pcs cookies", "missing x-apple-webauth-token",
        "termsupdateneeded", "access denied", "forbidden",
    )):
        return "auth"
    return "transient"
