from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    PROJECT_NAME: str = "ResolveAI Foundation"
    
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
    S3_ACCESS_KEY: str = Field(..., description="MinIO Access Key")
    S3_SECRET_KEY: str = Field(..., description="MinIO Secret Key")
    S3_BUCKET_NAME: str = Field(..., description="MinIO Bucket Name")
    
    # Security (Minimal)
    API_SECRET_KEY: str = Field(..., description="API Secret Key")
    RAZORPAY_WEBHOOK_SECRET: str = Field(..., description="Webhook HMAC verification secret")
    
    # OpenAI Settings
    OPENAI_API_KEY: str = Field("sk-dummy", description="OpenAI API Key")
    EMBEDDING_BATCH_SIZE: int = Field(100, description="Batch size for embedding generation")
    
    # Features
    ENABLE_DEV_ENDPOINTS: bool = Field(default=False, description="Enable development endpoints")

    # Module D: Document Intelligence Limits
    MAX_PDF_PAGES: int = Field(default=50, description="Maximum allowed pages in a PDF document")
    MAX_IMAGE_WIDTH: int = Field(default=8000, description="Maximum image width in pixels")
    MAX_IMAGE_HEIGHT: int = Field(default=8000, description="Maximum image height in pixels")
    MIN_NATIVE_TEXT_LENGTH: int = Field(default=50, description="Minimum characters for native text to be considered usable")

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
    MLFLOW_TRACKING_URI: str = Field(default="./mlruns", description="MLflow tracking URI — local file store by default, or the local/self-hosted mlflow compose service")

    # Module L: authoritative Step 15 held-out evaluation artifact (read-only).
    # Matches the existing root-level ML pipeline scripts' own convention of
    # writing to a plain "artifacts" directory relative to the process's
    # working directory — same convention as MLFLOW_TRACKING_URI above.
    MODEL_EVALUATION_ARTIFACT_DIR: str = Field(default="artifacts", description="Directory containing step15_final_holdout_eval_*/final_evaluation.json, written by the repo-root evaluate_holdout_step15.py script")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
