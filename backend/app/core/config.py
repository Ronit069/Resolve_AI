from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator

# Values known to be placeholders: either hardcoded as a code-level default
# (OPENAI_API_KEY/GROQ_API_KEY below) or the exact dev/test values this
# project's own local setup and .env.example have used. See
# backend/SECRETS.md for the full secret inventory.
_PLACEHOLDER_SECRET_VALUES = {
    "sk-dummy", "gsk_dummy", "testsecret", "dummysecret", "rzp_test_dummykeyid",
}
_PRODUCTION_LIKE_ENVIRONMENTS = {"production", "prod", "staging"}

class Settings(BaseSettings):
    PROJECT_NAME: str = "ResolveAI Foundation"

    ENVIRONMENT: str = Field(
        default="development",
        description="Deployment environment name. Set to 'production' or 'staging' to enable "
        "the startup check that refuses to boot with placeholder/dummy secret values "
        "(see backend/SECRETS.md).",
    )

    # Database
    POSTGRES_USER: str = Field(..., description="PostgreSQL User")
    POSTGRES_PASSWORD: str = Field(..., description="PostgreSQL Password")
    POSTGRES_DB: str = Field(..., description="PostgreSQL Database Name")
    POSTGRES_HOST: str = Field(..., description="PostgreSQL Host")
    POSTGRES_PORT: str = Field(..., description="PostgreSQL Port")

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Redis / Celery
    REDIS_URL: str = Field(..., description="Redis Connection URL")
    
    # Object Storage (MinIO)
    S3_ENDPOINT_URL: str = Field(..., description="MinIO/S3 Endpoint URL")
    # Optional (issue #14): local dev/CI always set these (MinIO requires
    # static credentials). A real AWS deployment can leave both unset to
    # run on IAM-role-based credentials (ECS task role / EKS IRSA /
    # instance profile) instead of long-lived static keys -- see
    # StorageClient, which only passes them to boto3 when both are set,
    # letting boto3's own default credential chain take over otherwise.
    S3_ACCESS_KEY: Optional[str] = Field(default=None, description="S3/MinIO Access Key (omit to use IAM-role credentials)")
    S3_SECRET_KEY: Optional[str] = Field(default=None, description="S3/MinIO Secret Key (omit to use IAM-role credentials)")
    S3_BUCKET_NAME: str = Field(..., description="MinIO Bucket Name")
    S3_REGION: str = Field(default="us-east-1", description="S3 bucket region. MinIO ignores this; a real AWS bucket outside us-east-1 must set it to match or requests fail.")
    
    # Security (Minimal)
    API_SECRET_KEY: str = Field(..., description="API Secret Key")
    RAZORPAY_WEBHOOK_SECRET: str = Field(..., description="Webhook HMAC verification secret")
    
    # OpenAI Settings
    OPENAI_API_KEY: str = Field("sk-dummy", description="OpenAI API Key")
    EMBEDDING_BATCH_SIZE: int = Field(100, description="Batch size for embedding generation")
    
    # Groq Settings
    GROQ_API_KEY: str = Field(default="gsk_dummy", description="Groq API Key")
    GROQ_MODEL: str = Field(default="openai/gpt-oss-20b", description="Groq model to use")
    
    # Features
    ENABLE_DEV_ENDPOINTS: bool = Field(default=False, description="Enable development endpoints")

    # Module L: structured logging + error tracking (issue #16)
    LOG_LEVEL: str = Field(default="INFO", description="Root logger level for the JSON logging configured in app/core/logging_config.py")
    SENTRY_DSN: Optional[str] = Field(default=None, description="Sentry error-tracking DSN. Unset (the default) means no Sentry SDK traffic at all -- see app/core/error_tracking.py")
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0.0, description="Sentry performance-tracing sample rate, 0.0-1.0. 0 disables tracing; error capture is unaffected either way")

    # Module D: Document Intelligence Limits
    MAX_PDF_PAGES: int = Field(default=50, description="Maximum allowed pages in a PDF document")
    MAX_IMAGE_WIDTH: int = Field(default=8000, description="Maximum image width in pixels")
    MAX_IMAGE_HEIGHT: int = Field(default=8000, description="Maximum image height in pixels")
    MIN_NATIVE_TEXT_LENGTH: int = Field(default=50, description="Minimum characters for native text to be considered usable")
    OCR_ENGINE: str = Field(
        default="deterministic",
        description="Which OCR adapter app.worker.tasks uses: 'deterministic' (canned output, "
        "for dev/test) or 'tesseract' (real OCR via pytesseract, requires the tesseract-ocr "
        "system package).",
    )

    # Module G: Retrieval
    RETRIEVAL_TOP_K: int = Field(default=5, description="Number of top results to return during knowledge retrieval")
    RETRIEVAL_SIMILARITY_THRESHOLD: float = Field(default=0.70, description="Minimum cosine similarity required for a knowledge chunk to be retrieved")

    # Module G: LLM Generation (G-05)
    LLM_MODEL: str = Field(default="gpt-4o-mini", description="OpenAI model to use for draft generation")
    LLM_TEMPERATURE: float = Field(default=0.0, description="Temperature for LLM generation (0 = deterministic)")
    # G-20 lineage configuration
    LLM_GUARDRAIL_VERSION: str = "v1"
    LLM_TIMEOUT_SECONDS: int = Field(default=30, description="Timeout in seconds for LLM API calls")
    LLM_PROMPT_TEMPLATE_VERSION: str = Field(default="v1", description="Version string for the G-05 prompt template")
    SUMMARY_MAX_LENGTH: int = Field(default=1000, description="Max characters for generated contest summary (Razorpay limit)")
    CITATION_COVERAGE_MIN: float = Field(default=0.5, description="Minimum fraction of claims that must have grounding; below this routes to REVIEW")

    # Module H: H-05 Dual Control
    DUAL_CONTROL_AMOUNT_THRESHOLD_MINOR: int = Field(default=5_000_000, description="Dispute amount (INR minor units/paise) at or above which APPROVE_CONTEST/APPROVE_ACCEPT require a second, distinct APPROVER (₹50,000)")

    # Module I: minimal browser-support CORS
    CORS_ALLOWED_ORIGIN: str = Field(default="http://localhost:5173", description="Exact origin the Module I frontend is served from (no wildcard)")

    # Module J, Category D: Razorpay external API credentials (global, not per-merchant)
    RAZORPAY_KEY_ID: str = Field(..., description="Razorpay API key ID used for Basic Auth against api.razorpay.com")
    RAZORPAY_KEY_SECRET: str = Field(..., description="Razorpay API key secret used for Basic Auth against api.razorpay.com")
    RAZORPAY_ALLOW_LIVE_MODE: bool = Field(default=False, description="Fail-closed guard: must be explicitly True to permit a live (non-test) RAZORPAY_KEY_ID against the real Razorpay API")

    # Module L: MLflow experiment tracking (local/self-hosted only per frozen PO decision).
    # Defaults to a local file store so training scripts work with zero external service.
    # Issue #15: mlflow>=3.0 put the bare-filesystem tracking backend
    # ("./mlruns" as a plain path) into maintenance mode -- it now raises
    # MlflowException instead of working, which silently broke this
    # default (log_training_run's try/except swallowed it, so training
    # kept succeeding, but no run was ever actually recorded). A
    # sqlite:/// URI is the same "local/self-hosted only" frozen PO
    # decision (no server process, no external/hosted service, no
    # production credential) and isn't deprecated.
    MLFLOW_TRACKING_URI: str = Field(default="sqlite:///mlflow.db", description="MLflow tracking URI — local SQLite file by default, or the local/self-hosted mlflow compose service")

    # Module L: authoritative Step 15 held-out evaluation artifact (read-only).
    # Matches the existing root-level ML pipeline scripts' own convention of
    # writing to a plain "artifacts" directory relative to the process's
    # working directory — same convention as MLFLOW_TRACKING_URI above.
    MODEL_EVALUATION_ARTIFACT_DIR: str = Field(default="artifacts", description="Directory containing step15_final_holdout_eval_*/final_evaluation.json, written by the repo-root evaluate_holdout_step15.py script")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    def is_production_like(self) -> bool:
        """True when ENVIRONMENT is production/prod/staging (case-insensitive).
        Gates the X-User-Id dev-auth fallback in app/api/deps.py -- see
        backend/SECRETS.md and app/core/security.py."""
        return self.ENVIRONMENT.lower() in _PRODUCTION_LIKE_ENVIRONMENTS

    @model_validator(mode="after")
    def _reject_placeholder_secrets_outside_dev(self) -> "Settings":
        """
        A real deployment accidentally left on .env.example-style
        placeholders, or on the OPENAI_API_KEY/GROQ_API_KEY code defaults,
        fails loudly and immediately rather than starting up and only
        failing later when a real API call hits a dummy key. Local/dev/test
        runs (ENVIRONMENT left at its "development" default) are unaffected.
        """
        if self.ENVIRONMENT.lower() not in _PRODUCTION_LIKE_ENVIRONMENTS:
            return self

        secret_fields = {
            "POSTGRES_PASSWORD": self.POSTGRES_PASSWORD,
            "S3_ACCESS_KEY": self.S3_ACCESS_KEY,
            "S3_SECRET_KEY": self.S3_SECRET_KEY,
            "API_SECRET_KEY": self.API_SECRET_KEY,
            "RAZORPAY_WEBHOOK_SECRET": self.RAZORPAY_WEBHOOK_SECRET,
            "RAZORPAY_KEY_ID": self.RAZORPAY_KEY_ID,
            "RAZORPAY_KEY_SECRET": self.RAZORPAY_KEY_SECRET,
            "OPENAI_API_KEY": self.OPENAI_API_KEY,
            "GROQ_API_KEY": self.GROQ_API_KEY,
        }

        offending = [
            name
            for name, value in secret_fields.items()
            if value and (
                value in _PLACEHOLDER_SECRET_VALUES
                or value.lower().startswith("your_")
                or value.lower().endswith("_here")
            )
        ]

        if offending:
            raise ValueError(
                f"Refusing to start with ENVIRONMENT='{self.ENVIRONMENT}' while these settings "
                f"still hold placeholder/dummy values: {', '.join(offending)}. Set real secrets "
                "(see backend/SECRETS.md) before deploying outside local development."
            )
        return self

settings = Settings()
