from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings for one gateway process. Every endpoint is required: there is no default to point at."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    entra_tenant_id: str
    api_audience: str
    admin_role: str = "Platform.Admin"
    tenant_role_prefix: str = "tenant:"
    tenant_scope: str = ""
    require_auth: bool = True
    redis_host: str
    redis_port: int = Field(default=10000, ge=1, le=65535)
    cosmos_endpoint: str
    cosmos_database: str = "saas"
    tenants_container: str = "tenants"
    credentials_container: str = "credentials"
    usage_container: str = "usage"
    servicebus_namespace: str
    usage_queue: str = "usage"
    onboarding_queue: str = "onboarding"
    usage_stream: str = "usage"
    eventgrid_endpoint: str
    foundry_endpoint: str
    embedding_deployment: str = "text-embedding-3-small"
    search_endpoint: str
    shared_index: str = "regulations"
    keyvault_url: str
    signing_key_name: str = "credential-signing"
    upstream_url: str
    upstream_audience: str
    upstream_timeout_s: float = Field(default=90.0, gt=0)
    breaker_failures: int = Field(default=5, ge=1)
    breaker_reset_s: float = Field(default=30.0, gt=0)
    cache_similarity: float = Field(default=0.92, ge=0.0, le=1.0)
    pii_document_characters: int = Field(default=5120, ge=1, le=5120)
    pii_documents_per_request: int = Field(default=5, ge=1, le=5)
    applicationinsights_connection_string: str | None = None

    @property
    def upstream_scope(self) -> str:
        return f"{self.upstream_audience}/.default"
