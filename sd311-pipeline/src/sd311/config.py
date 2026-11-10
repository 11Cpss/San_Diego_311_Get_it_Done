"""Configuration for the Lambdas.

Rule of thumb used in this project:
  * Non-secret settings (bucket name, URLs, timeouts) -> environment variables,
    set by Terraform on the Lambda function.
  * Secrets (Databricks host/token/job id) -> SSM Parameter Store SecureStrings,
    fetched at runtime. They never appear in Terraform state or env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://seshat.datasd.org/get_it_done_reports/"
# The city's server sometimes rejects non-browser clients, so we look like one.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 sd311-pipeline/0.1"
)


@dataclass(frozen=True)
class Settings:
    bucket: str
    base_url: str = DEFAULT_BASE_URL
    user_agent: str = DEFAULT_USER_AGENT
    http_timeout_s: int = 60
    http_retries: int = 3
    metric_namespace: str = "SD311"
    timezone: str = "America/Los_Angeles"
    ssm_prefix: str = "/sd311/databricks"
    # Unity Catalog volume root that the Databricks agent's job reads from.
    volume_root: str = "/Volumes/sd311/bronze/landing"
    summary_root: str = "/Volumes/sd311/ml/run_summaries"
    # Set to "false" to skip the Databricks upload/trigger (useful before
    # the workspace exists: S3 landing still works).
    databricks_enabled: bool = True
    extra: dict = field(default_factory=dict)


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    """Build Settings from environment variables (fails fast if BUCKET is unset)."""
    bucket = os.environ.get("SD311_BUCKET")
    if not bucket:
        raise RuntimeError("SD311_BUCKET environment variable is required")
    return Settings(
        bucket=bucket,
        base_url=os.environ.get("SD311_BASE_URL", DEFAULT_BASE_URL),
        user_agent=os.environ.get("SD311_USER_AGENT", DEFAULT_USER_AGENT),
        http_timeout_s=int(os.environ.get("SD311_HTTP_TIMEOUT_S", "60")),
        http_retries=int(os.environ.get("SD311_HTTP_RETRIES", "3")),
        metric_namespace=os.environ.get("SD311_METRIC_NAMESPACE", "SD311"),
        timezone=os.environ.get("SD311_TIMEZONE", "America/Los_Angeles"),
        ssm_prefix=os.environ.get("SD311_SSM_PREFIX", "/sd311/databricks"),
        volume_root=os.environ.get("SD311_VOLUME_ROOT", "/Volumes/sd311/bronze/landing"),
        summary_root=os.environ.get("SD311_SUMMARY_ROOT", "/Volumes/sd311/ml/run_summaries"),
        databricks_enabled=_bool(os.environ.get("SD311_DATABRICKS_ENABLED"), True),
    )
