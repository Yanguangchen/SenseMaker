"""Shared AI failures and credential-safe diagnostics."""

import re


class AIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = status_code in {408, 429, 500, 502, 503, 504}


def redact(text: str, api_key: str) -> str:
    if api_key:
        text = text.replace(api_key, "[redacted]")
    text = re.sub(r"AIza[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]+", "[redacted]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)(?:key|api_key|x-goog-api-key)\s*[=:]\s*[^\s&\"']+", "key=[redacted]", text)
    return " ".join(text.split())[:800]
