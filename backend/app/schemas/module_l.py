from typing import Dict, Optional

from pydantic import BaseModel


class RuntimeMetricCategory(BaseModel):
    sample_count: int
    error_count: int
    avg_latency_ms: Optional[float] = None
    min_latency_ms: Optional[float] = None
    max_latency_ms: Optional[float] = None
    error_rate: Optional[float] = None


class RuntimeMetricsResponse(BaseModel):
    categories: Dict[str, RuntimeMetricCategory]
