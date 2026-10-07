"""Domain exceptions. The API layer maps these to HTTP responses."""


class PortTariffError(Exception):
    """Base class for all expected, domain-level failures."""


class ConfigurationError(PortTariffError):
    """Required configuration (e.g. the LLM API key) is missing or invalid."""


class DocumentParsingError(PortTariffError):
    """The tariff document could not be read or has no extractable text."""


class LLMServiceError(PortTariffError):
    """The LLM provider is unavailable, rate-limited, or returned bad output."""


class RuleExtractionError(PortTariffError):
    """No usable tariff rules could be extracted from the document."""


class InvalidTariffRuleError(PortTariffError):
    """An extracted rule is incomplete or cannot be computed."""
