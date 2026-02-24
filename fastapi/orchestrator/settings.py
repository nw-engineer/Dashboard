import os
from dataclasses import dataclass
from typing import List, Optional

from .dotenv_simple import load_dotenv
from .important_spec import ImportantFieldsSpec


def _csv_env(name: str) -> List[str]:
    v = (os.getenv(name) or "").strip()
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


@dataclass
class OrchestratorSettings:
    """Runtime settings for generation + project-specific important fields."""

    gen_api_url: Optional[str]
    gen_api_timeout_s: int
    gen_api_include_keys: List[str]
    gen_api_response_text_keys: List[str]
    auto_generate_base: bool
    required_fields: List[str]
    important_spec: ImportantFieldsSpec

    @staticmethod
    def from_env() -> "OrchestratorSettings":
        # Load .env if present (no-op if missing)
        load_dotenv(os.getenv("DOTENV_PATH") or ".env")

        gen_api_url = (os.getenv("GEN_API_URL") or "").strip() or None
        gen_api_timeout_s = int(os.getenv("GEN_API_TIMEOUT", "30"))

        # For custom endpoints (e.g., lab/company-style payload), list top-level keys to include.
        include_keys = _csv_env("GEN_API_INCLUDE_KEYS")
        if not include_keys:
            # Backward-compatible defaults + new endpoint defaults
            include_keys = [
                "user_input",
                "talent",
                "company",
                "comapny",  # some endpoints use this typo
                "lab",
                "config",
                "system_prompt",
                "prompt",
                "messages",
                "output_format",
                "output_json_schema",
                "max_length",
                "temperature",
                "model",
                "arch_version",
                "prompt_version",
                "last_updated",
            ]

        response_text_keys = _csv_env("GEN_API_RESPONSE_TEXT_KEYS")
        if not response_text_keys:
            response_text_keys = [
                "offer_recommendation",
                "gen_lab_company_description",
                "generated_text",
                "text",
                "output",
                "result",
            ]

        auto_generate_base = os.getenv("AUTO_GENERATE_BASE", "1") == "1"

        required_fields = _csv_env("REQUIRED_FIELDS")
        # If not set, keep default (talent/company/generated_text) in RuleEngine,
        # but allow evaluator to relax dynamically when using auto generation.

        important_spec = ImportantFieldsSpec.load_from_env()

        return OrchestratorSettings(
            gen_api_url=gen_api_url,
            gen_api_timeout_s=gen_api_timeout_s,
            gen_api_include_keys=include_keys,
            gen_api_response_text_keys=response_text_keys,
            auto_generate_base=auto_generate_base,
            required_fields=required_fields,
            important_spec=important_spec,
        )
