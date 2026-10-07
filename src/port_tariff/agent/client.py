import json
import re
from typing import Type, TypeVar

from openai import (
    APIConnectionError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel

from port_tariff.config import get_settings
from port_tariff.exceptions import ConfigurationError, LLMServiceError
from port_tariff.logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Wrapper around the OpenAI-compatible client (supporting Gemini, OpenAI, etc.)

    with structured JSON schema extraction.
    """

    def __init__(self):
        settings = get_settings()
        if not settings.llm_api_key:
            raise ConfigurationError(
                "LLM_API_KEY environment variable is not set. Please set it in your .env file."
            )

        # The SDK retries 429/5xx/timeouts with exponential backoff
        # (honouring Retry-After), which matters on free-tier rate limits.
        self.client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        logger.info(
            "llm.client.initialized",
            base_url=settings.llm_base_url,
            model=self.model,
            temperature=self.temperature,
        )

    # ------------------------------------------------------------------
    # Robust JSON extraction.
    #
    # Not all OpenAI-compatible endpoints honour `response_format`. Models
    # commonly return:
    #   - raw JSON                          -> '{"a": 1}'
    #   - markdown-fenced JSON              -> '```json\n{"a": 1}\n```'
    #   - prose + JSON blob                 -> 'Here is the result:\n{...}'
    # The helpers below extract a usable JSON payload from any of these.
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json_blob(text: str) -> str:
        """Extract a JSON object/array from a possibly prose-wrapped or
        markdown-fenced LLM response. Returns the input unchanged if no
        JSON-looking blob can be located."""
        if not text:
            return text

        stripped = text.strip()

        # 1) Unwrap a fenced code block if present (```json ... ``` or ``` ... ```).
        fence = re.search(
            r"```(?:json)?\s*(.*?)```",
            stripped,
            re.DOTALL | re.IGNORECASE,
        )
        if fence:
            stripped = fence.group(1).strip()

        # 2) Fast path: already a JSON document.
        if stripped.startswith(("{", "[")):
            return stripped

        # 3) Scan for the first balanced {...} or [...] payload.
        for opener, closer in (("{", "}"), ("[", "]")):
            start = stripped.find(opener)
            if start == -1:
                continue
            depth = 0
            in_str = False
            escape = False
            for i in range(start, len(stripped)):
                ch = stripped[i]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        return stripped[start : i + 1]

        return stripped

    @classmethod
    def _parse_json_response(cls, content: str, response_model: Type[T]) -> T:
        """Parse a raw LLM string into `response_model`, tolerating fenced
        or prose-wrapped JSON. Raises ValueError, carrying both the extracted
        candidate's parse error and the model name, when nothing usable can
        be extracted. Re-parsing the raw content would have produced the same
        failure while pointing at the wrong string, so it is not attempted."""
        candidate = cls._extract_json_blob(content)
        try:
            return response_model.model_validate_json(candidate)
        except Exception as exc:
            raise ValueError(
                f"Could not parse LLM response as {response_model.__name__} "
                f"from extracted JSON candidate ({type(exc).__name__}): {exc}"
            ) from exc

    def extract_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
    ) -> T:
        """Invokes the model and parses the response directly into the specified Pydantic model."""
        schema_name = response_model.__name__
        logger.info(
            "llm.call.start",
            model=self.model,
            schema=schema_name,
            system_prompt_chars=len(system_prompt),
            user_prompt_chars=len(user_prompt),
        )
        logger.debug(
            "llm.call.prompts",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        # First attempt: modern structured outputs using client.beta.chat.completions.parse
        try:
            completion = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_model,
                temperature=self.temperature,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is not None:
                logger.info("llm.call.structured_ok", schema=schema_name)
                return parsed
            logger.warning(
                "llm.call.structured_empty",
                schema=schema_name,
                note="structured endpoint returned None; falling back to JSON mode",
            )
        except (RateLimitError, APIConnectionError, AuthenticationError) as exc:
            # Already retried by the SDK; a JSON-mode fallback cannot fix these.
            logger.error(
                "llm.call.unavailable",
                schema=schema_name,
                error_type=type(exc).__name__,
            )
            raise LLMServiceError(
                f"LLM provider unavailable ({type(exc).__name__}). "
                "Check credentials, rate limits and connectivity."
            ) from exc
        except Exception as structured_exc:
            # Non-fatal: fall back to plain JSON mode. Logged at WARNING so
            # schema/model regressions are visible instead of silently swallowed.
            logger.warning(
                "llm.call.structured_failed_fallback",
                schema=schema_name,
                error_type=type(structured_exc).__name__,
                error=str(structured_exc),
            )

        # Fallback attempt: Standard JSON Mode with a hardened output contract.
        # Some OpenAI-compatible proxies ignore `response_format` entirely, so
        # we also enforce the contract in the prompt text itself.
        logger.debug("llm.call.fallback_json_mode", schema=schema_name)
        strict_instruction = (
            "\n\nCRITICAL OUTPUT CONTRACT:\n"
            "- Respond with a SINGLE RAW JSON object only.\n"
            "- Do NOT wrap the response in markdown code fences (no ```json).\n"
            "- Do NOT include any prose, commentary, preamble, or trailing text.\n"
            "- Your first character MUST be '{' and your last character MUST be '}'.\n"
            "- The JSON MUST conform to this schema:\n"
            f"{json.dumps(response_model.model_json_schema())}"
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"{user_prompt}{strict_instruction}"},
                ],
                response_format={"type": "json_object"},
                temperature=self.temperature,
            )
            content = response.choices[0].message.content or "{}"
            logger.debug(
                "llm.call.fallback.raw_response",
                schema=schema_name,
                chars=len(content),
                preview=content[:200],
            )
            parsed_model = self._parse_json_response(content, response_model)
            logger.info("llm.call.fallback_ok", schema=schema_name)
            return parsed_model
        except Exception as fallback_exc:
            logger.exception(
                "llm.call.failed",
                schema=schema_name,
                error_type=type(fallback_exc).__name__,
            )
            raise LLMServiceError(
                f"LLM call failed or returned invalid {schema_name} output "
                f"({type(fallback_exc).__name__})."
            ) from fallback_exc
