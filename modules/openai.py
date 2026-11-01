"""Server-side OpenAI Responses transport for ChatGPT analysis."""

from __future__ import annotations

import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

from modules.ai_errors import AIError, redact

API_URL = "https://api.openai.com/v1/responses"


class OpenAIError(AIError):
    pass


def api_error(exc: HTTPError, api_key: str) -> OpenAIError:
    hints = {
        400: "OpenAI rejected the request. Check the configured model and its supported features.",
        401: "The server's OpenAI API key was not accepted. Contact the app administrator.",
        403: "Access was denied. Check the server key's project and model permissions.",
        404: "The configured OpenAI model is unavailable. Contact the app administrator.",
        429: "OpenAI quota or rate limit reached. Check the project's quota and billing, then retry.",
        503: "OpenAI is temporarily unavailable. Try again later.",
    }
    detail = ""
    try:
        error = json.loads(exc.read(16384)).get("error", {})
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            detail = redact(error["message"], api_key)
    except (ValueError, OSError, AttributeError):
        pass
    message = f"OpenAI HTTP {exc.code}: {hints.get(exc.code, 'OpenAI could not complete the request.')}"
    if detail:
        message += " OpenAI: " + detail
    return OpenAIError(message, status_code=exc.code)


def generate_content(prompt: str, api_key: str, model_name: str, timeout: float = 90, *,
                     schema: dict | None = None, temperature: float | None = None,
                     max_output_tokens: int | None = None) -> str:
    """Return the model's JSON text.

    Without ``schema`` the response is free-form JSON mode. With ``schema`` it uses
    strict Structured Outputs, so the object root, required fields and enums hold.
    """
    if not api_key.strip():
        raise OpenAIError("ChatGPT is not configured. Contact the app administrator.")
    text_format = ({"type": "json_schema", "name": "sensemaker_response", "schema": schema, "strict": True}
                   if schema is not None else {"type": "json_object"})
    body = {"model": model_name, "input": prompt, "store": False, "text": {"format": text_format}}
    if temperature is not None:
        body["temperature"] = temperature
    if max_output_tokens is not None:
        body["max_output_tokens"] = max_output_tokens
    request = Request(API_URL, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
    })
    try:
        with urlopen(request, timeout=timeout, context=ssl.create_default_context(cafile=certifi.where())) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise api_error(exc, api_key) from None
    except URLError as exc:
        raise OpenAIError("OpenAI connection failed: " + redact(str(exc.reason), api_key)) from None
    except TimeoutError:
        raise OpenAIError(f"OpenAI timed out after {timeout:.0f} seconds. Try fewer or shorter records.") from None
    except ValueError:
        raise OpenAIError("OpenAI returned an invalid API response. Try again later.") from None
    if not isinstance(result, dict):
        raise OpenAIError("OpenAI returned an invalid API response.")
    if result.get("status") != "completed":
        details = result.get("incomplete_details") or result.get("error") or {}
        reason = details.get("reason") or details.get("message") if isinstance(details, dict) else None
        raise OpenAIError("OpenAI did not return a complete analysis ("
                          + redact(str(reason or result.get("status", "unknown status")), api_key)
                          + "). Try fewer or shorter records.")
    output = result.get("output")
    if not isinstance(output, list):
        raise OpenAIError("OpenAI returned an invalid API response.")
    texts = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise OpenAIError("OpenAI returned an invalid API response.")
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise OpenAIError("OpenAI declined to analyze this content. Review the selected records before retrying.")
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    text = "".join(texts)
    if not text.strip():
        raise OpenAIError("OpenAI returned no analysis text. Review the selected records before retrying.")
    return text
