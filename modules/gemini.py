"""Gemini model discovery and safe, actionable API errors."""

from __future__ import annotations

import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi

from modules.ai_errors import AIError, redact

AUTO_MODEL = "auto"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta"


class GeminiError(AIError):
    pass


def api_error(exc: HTTPError, api_key: str) -> GeminiError:
    hints = {
        400: "Google rejected the request. Check the API key and the model's supported features.",
        401: "The server's Gemini API key was not accepted. Contact the app administrator.",
        403: "Access was denied. Check this key's API restrictions and project permissions.",
        404: "The model is unavailable for this key or API method. Choose Gemini (automatic) or another model.",
        429: "Gemini quota or rate limit reached. Check your project's quota and billing, then retry.",
        503: "Gemini is temporarily unavailable. Try again later.",
    }
    detail = ""
    try:
        error = json.loads(exc.read(16384)).get("error", {})
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            detail = redact(error["message"], api_key)
    except (ValueError, OSError, AttributeError):
        pass
    message = f"Gemini HTTP {exc.code}: {hints.get(exc.code, 'Google could not complete the request.')}"
    if detail:
        message += " Google: " + detail
    return GeminiError(message, status_code=exc.code)


def normalize_model_name(name: str) -> str:
    name = (name or AUTO_MODEL).strip()
    if name.lower() == AUTO_MODEL:
        return AUTO_MODEL
    if not name.startswith("models/"):
        name = "models/" + name
    if not re.fullmatch(r"models/[A-Za-z0-9._-]+", name):
        raise GeminiError("Choose Gemini (automatic) or a model from the list.")
    return name


def is_text_model(model: dict) -> bool:
    name = model.get("name", "")
    return (isinstance(name, str) and re.fullmatch(r"models/gemini-[A-Za-z0-9._-]+", name) is not None
            and "generateContent" in model.get("supportedGenerationMethods", [])
            and not re.search(r"image|tts|audio|live|transcrib|robotic|computer-use|embedding|nano-banana|omni", name))


def list_text_models(api_key: str) -> list[str]:
    if not api_key.strip():
        raise GeminiError("Gemini is not configured. Contact the app administrator.")
    names, seen_tokens = set(), set()
    token = ""
    while True:
        params = {"pageSize": 1000}
        if token:
            params["pageToken"] = token
        request = Request(API_ROOT + "/models?" + urlencode(params), headers={"x-goog-api-key": api_key})
        try:
            with urlopen(request, timeout=20, context=ssl.create_default_context(cafile=certifi.where())) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise api_error(exc, api_key) from None
        except (URLError, TimeoutError):
            raise GeminiError("Could not reach Google's model catalogue. Check the connection and try again.") from None
        except ValueError:
            raise GeminiError("Google returned an invalid model catalogue. Try again later.") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("models", []), list):
            raise GeminiError("Google returned an invalid model catalogue. Try again later.")
        names.update(m["name"] for m in payload.get("models", []) if isinstance(m, dict) and is_text_model(m))
        token = payload.get("nextPageToken")
        if not token:
            break
        if not isinstance(token, str) or token in seen_tokens:
            raise GeminiError("Google returned an incomplete model catalogue. Refresh and try again.")
        seen_tokens.add(token)
    if not names:
        raise GeminiError("No Gemini text models supporting generateContent were returned for this API key.")
    return sorted(names)


_GENERAL_STABLE = re.compile(r"models/gemini-(\d+)(?:\.(\d+))?-(flash|flash-lite|pro)")


def picker_models(available: list[str]) -> list[str]:
    """Stable general-purpose models, newest first; dated, preview and specialist variants are hidden."""
    def version(name: str) -> tuple:
        match = _GENERAL_STABLE.fullmatch(name)
        return int(match[1]), int(match[2] or 0), ["pro", "flash", "flash-lite"].index(match[3])
    return sorted((name for name in available if _GENERAL_STABLE.fullmatch(name)),
                  key=lambda name: (-version(name)[0], -version(name)[1], version(name)[2]))


def display_name(name: str) -> str:
    """models/gemini-3.5-flash-lite -> Gemini 3.5 Flash-Lite."""
    words = name.removeprefix("models/").split("-")
    return " ".join(w.capitalize() if not w[:1].isdigit() else w for w in words).replace("Flash Lite", "Flash-Lite")


def resolve_model(api_key: str, requested: str) -> str:
    name = normalize_model_name(requested)
    available = list_text_models(api_key)
    if name != AUTO_MODEL:
        if name not in available:
            raise GeminiError(f"{name} is not available for this key with generateContent. "
                              "Choose Gemini (automatic) or another model.")
        return name
    # Choose general text Flash models, not specialized variants such as Flash Cyber.
    general_flash = r"models/gemini-(?:\d+(?:\.\d+)?-)?flash(?:-lite)?(?:-(?:latest|\d{3}|preview(?:-[\w.-]+)?|exp(?:-[\w.-]+)?))?"
    flash = [n for n in available if re.fullmatch(general_flash, n)]
    if not flash:
        raise GeminiError("No Flash text model is available for automatic selection. "
                          "Choose a specific Gemini model instead.")

    def rank(model: str):
        version = re.search(r"gemini-(\d+)(?:\.(\d+))?", model)
        numbers = (int(version[1]), int(version[2] or 0)) if version else (0, 0)
        stable = not re.search(r"preview|experimental|(?:^|-)exp(?:-|$)", model)
        # Classification batches should favor the lower-latency Lite family.
        return stable, "lite" in model, numbers, model

    return max(flash, key=rank)
