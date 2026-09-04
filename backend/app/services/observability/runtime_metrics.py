"""
Module L, L-04 — runtime/infra observability.

Deliberately separate from H-22's queue_metrics.py, which owns
human-review-queue metrics (queue age, near-deadline, review turnaround)
and is frozen/additive-only. This module covers a different, previously
unowned surface: inference/OCR/LLM latency, task ("queue") duration, and
error rate for the underlying pipeline operations — never merged with
H-22's own metrics or semantics.

In-process, in-memory only (no new table, no external metrics backend) —
a deliberately small L-04 slice. Samples are capped per category so
memory usage stays bounded across a long-running process; this is an
observability aid, not a durable metrics store.
"""

import functools
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Dict, Optional

_MAX_SAMPLES_PER_CATEGORY = 500

# Frozen set of L-04 categories. "inference" is included for completeness
# per the blueprint's L-04 list, even though no current production code
# path in this repository performs live model-serving inference — Module
# F does not yet have a live-scoring endpoint/task, only offline
# training/evaluation scripts. Nothing here fabricates a call site for it.
KNOWN_CATEGORIES = ("inference", "ocr", "llm", "queue_duration")

_lock = threading.Lock()
_latencies_ms: Dict[str, deque] = {cat: deque(maxlen=_MAX_SAMPLES_PER_CATEGORY) for cat in KNOWN_CATEGORIES}
_error_counts: Dict[str, int] = {cat: 0 for cat in KNOWN_CATEGORIES}


def _ensure_category(category: str) -> None:
    if category not in _latencies_ms:
        _latencies_ms[category] = deque(maxlen=_MAX_SAMPLES_PER_CATEGORY)
        _error_counts[category] = 0


def record_latency(category: str, milliseconds: float) -> None:
    with _lock:
        _ensure_category(category)
        _latencies_ms[category].append(milliseconds)


def record_error(category: str) -> None:
    with _lock:
        _ensure_category(category)
        _error_counts[category] += 1


@contextmanager
def track_latency(category: str):
    """
    Context manager: records elapsed wall time (ms) to `category` on
    exit, and increments that category's error count if the wrapped code
    raised. Never suppresses the original exception.
    """
    start = time.monotonic()
    try:
        yield
    except Exception:
        record_error(category)
        raise
    finally:
        record_latency(category, (time.monotonic() - start) * 1000.0)


def track_latency_decorator(category: str):
    """Function decorator form of track_latency, for wrapping whole callables (e.g. Celery tasks)."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with track_latency(category):
                return func(*args, **kwargs)
        return wrapper
    return decorator


def get_runtime_metrics_summary() -> dict:
    with _lock:
        summary = {}
        for category in _latencies_ms:
            samples = list(_latencies_ms[category])
            error_count = _error_counts.get(category, 0)
            total = len(samples) + error_count
            summary[category] = {
                "sample_count": len(samples),
                "error_count": error_count,
                "avg_latency_ms": (sum(samples) / len(samples)) if samples else None,
                "min_latency_ms": min(samples) if samples else None,
                "max_latency_ms": max(samples) if samples else None,
                "error_rate": (error_count / total) if total > 0 else None,
            }
        return summary


def reset_runtime_metrics() -> None:
    """Test-only helper — never called from production code."""
    with _lock:
        for category in list(_latencies_ms.keys()):
            _latencies_ms[category].clear()
            _error_counts[category] = 0
