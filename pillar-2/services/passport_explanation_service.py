"""Gemini wording layer for already-reconciled public passport facts.

This service cannot read the database or blockchain and never selects or changes
reconciliation status. It receives a strict allowlist of facts assembled by
PassportFactsService. If configuration, model output, or the provider fails, it
returns the deterministic summary instead.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-flash"
ALLOWED_RECONCILIATION_STATUSES = {
    "MATCH",
    "CONFLICT",
    "PENDING_SYNC",
    "OFFCHAIN_ONLY",
    "CHAIN_ONLY",
    "SOURCE_UNAVAILABLE",
    "PARTIAL",
}
ALLOWED_CHECK_FIELDS = {
    "lifecycle_state",
    "ownership_commitment",
    "configuration_commitment",
    "verification_stamp",
}
ALLOWED_CHECK_RESULTS = {"MATCH", "CONFLICT", "NOT_COMPARABLE"}
ALLOWED_EVENT_TYPES = {
    "REGISTERED",
    "VERIFIED",
    "TRANSFERRED",
    "HARDWARE_CHANGED",
    "RECYCLED",
    "EXPORTED",
}
ALLOWED_SOURCE_VALUES = {
    "AVAILABLE",
    "UNAVAILABLE",
    "COMPLETE",
    "PARTIAL",
    "UNKNOWN",
}
SYSTEM_INSTRUCTION = """You are a plain-language translator for a device's digital lifecycle passport. Your job is to take raw technical JSON data and explain it clearly to a non-technical user.

Your goal is to make the passport history easy to understand while strictly relying ONLY on the provided data. Do not make up facts, guess causes, infer ownership, or invent physical-world events.

Follow these guidelines to structure your explanation:

1. Overall Status: Start with a clear, simple summary of whether the device's records are fully synchronized (MATCH) or if there are discrepancies (CONFLICT).
2. Explain the "Why": If there is a CONFLICT, do not just copy the headline. Look at the `checks` array and explicitly state which specific field failed (e.g., "The verification stamp's validity flag differs between the database and the blockchain") so the user knows exactly where the issue lies.
3. Summarize the History: Look at the `timeline`. Explain how many lifecycle events have been recorded and highlight the most recent activity in plain English. 
4. Plain English Limitations: Translate technical limitations into simple warnings (e.g., "Note: This is a read-only historical record"). Do not include raw transaction hashes, ledger round numbers, or backend ID keys.
5. Format for Readability: Use short paragraphs, bullet points, and bold text to make the information highly readable. Be thorough and explanatory, but never invent information outside the supplied JSON facts."""

_CACHE: "OrderedDict[str, tuple[float, Dict[str, Any]]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX_ITEMS = 256
_RATE_LOCK = threading.Lock()
_RECENT_CALLS = deque()


def _enum_or_none(value: Any, allowed: set[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    for allowed_value in allowed:
        if candidate == allowed_value.upper():
            return allowed_value
    return None


def _short_text(value: Any, max_chars: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text:
        return None
    return text[:max_chars]


class PassportExplanationService:
    """Generate wording only; leave facts and reconciliation outcomes untouched."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        cache_ttl_seconds: Optional[int] = None,
        http_post: Optional[Callable[..., Any]] = None,
        max_calls_per_minute: Optional[int] = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = (model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)).strip() or DEFAULT_MODEL
        self.timeout_ms = int(timeout_ms or os.getenv("GEMINI_TIMEOUT_MS", "12000"))
        self.cache_ttl_seconds = max(
            0,
            int(cache_ttl_seconds if cache_ttl_seconds is not None else os.getenv("PASSPORT_GEMINI_CACHE_TTL_SECONDS", "300")),
        )
        # Injectable for unit tests; production uses requests.post directly.
        self.http_post = http_post or requests.post
        self.max_calls_per_minute = max(
            1,
            int(max_calls_per_minute if max_calls_per_minute is not None else os.getenv("PASSPORT_GEMINI_MAX_CALLS_PER_MINUTE", "30")),
        )

    def explain(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        """Return an AI-written explanation or a safe deterministic fallback."""
        fallback = self._fallback(facts, "api_key_not_configured")
        if not self.api_key:
            return fallback

        safe_payload = self._build_safe_payload(facts)
        payload_text = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        cache_key = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()

        if self.cache_ttl_seconds > 0:
            now = time.monotonic()
            with _CACHE_LOCK:
                cached = _CACHE.get(cache_key)
                if cached and now - cached[0] <= self.cache_ttl_seconds:
                    _CACHE.move_to_end(cache_key)
                    return dict(cached[1])
                if cached:
                    _CACHE.pop(cache_key, None)

        if not self._take_generation_slot():
            return self._fallback(facts, "local_rate_limit")

        try:
            model_path = quote(self.model, safe="-._")
            endpoint = (
                "https://generativelanguage.googleapis.com/v1beta/"
                f"models/{model_path}:generateContent"
            )
            prompt = (
                "Explain these precomputed facts in simple language. Do not decide status.\n"
                f"FACTS_JSON:\n{payload_text}"
            )
            response = self.http_post(
                endpoint,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                },
                json={
                    "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048},
                },
                timeout=max(0.1, self.timeout_ms / 1000.0),
            )
            response.raise_for_status()
            response_data = response.json()
            generated_text = self._extract_response_text(response_data)

            cleaned = self._validate_model_text(generated_text)
            if cleaned is None:
                return self._fallback(facts, "invalid_model_response")

            result = {
                "provider": "gemini",
                "generated": True,
                "model": self.model,
                "text": cleaned,
                "fallback_reason": None,
            }
            if self.cache_ttl_seconds > 0:
                with _CACHE_LOCK:
                    _CACHE[cache_key] = (time.monotonic(), dict(result))
                    _CACHE.move_to_end(cache_key)
                    while len(_CACHE) > _CACHE_MAX_ITEMS:
                        _CACHE.popitem(last=False)
            return result
        except Exception as exc:
            # Provider messages can contain request details; log only exception type.
            logger.warning("Gemini passport explanation failed (exception_type=%s)", type(exc).__name__)
            return self._fallback(facts, "generation_failed")

    def _take_generation_slot(self) -> bool:
        """Limit unique uncached model calls per process to reduce public-route abuse.

        This is intentionally a small in-process safeguard, not a replacement
        for provider quotas or a distributed rate limiter at deployment ingress.
        """
        now = time.monotonic()
        with _RATE_LOCK:
            while _RECENT_CALLS and now - _RECENT_CALLS[0] >= 60:
                _RECENT_CALLS.popleft()
            if len(_RECENT_CALLS) >= self.max_calls_per_minute:
                return False
            _RECENT_CALLS.append(now)
            return True

    @staticmethod
    def _extract_response_text(response_data: Any) -> Optional[str]:
        """Extract candidate text without trusting arbitrary response fields."""
        if not isinstance(response_data, dict):
            return None
        candidates = response_data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return None
        first = candidates[0]
        if not isinstance(first, dict):
            return None
        # A larger token budget is allowed, but never present a provider-marked
        # truncated answer as though it were a complete passport explanation.
        finish_reason = first.get("finishReason")
        if isinstance(finish_reason, str) and finish_reason.upper() == "MAX_TOKENS":
            return None
        content = first.get("content")
        if not isinstance(content, dict):
            return None
        parts = content.get("parts")
        if not isinstance(parts, list):
            return None
        text_parts = [part.get("text") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)]
        return "\n".join(text_parts) if text_parts else None

    @staticmethod
    def _validate_model_text(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        cleaned = " ".join(value.split()).strip()
        # Refuse empty/abnormally long output. Frontend still renders all
        # authoritative deterministic checks separately regardless.
        if len(cleaned) < 20 or len(cleaned) > 1400:
            return None
        # Do not display obviously cut-off prose (for example, a response ending
        # mid-sentence after a provider output limit). Use the deterministic fallback.
        if cleaned[-1] not in ".?!\'\")]}":
            return None
        lowered = cleaned.lower()
        forbidden_claims = (
            "physically recycled",
            "ownership is verified",
            "guaranteed authentic",
            "tamper-proof",
            "definitely legitimate",
        )
        if any(phrase in lowered for phrase in forbidden_claims):
            return None
        return cleaned

    @staticmethod
    def _build_safe_payload(facts: Dict[str, Any]) -> Dict[str, Any]:
        reconciliation = facts.get("reconciliation") if isinstance(facts.get("reconciliation"), dict) else {}
        status = _enum_or_none(reconciliation.get("status"), ALLOWED_RECONCILIATION_STATUSES) or "SOURCE_UNAVAILABLE"
        headline = _short_text(reconciliation.get("headline"), 240) or "Reconciliation could not be fully completed."

        checks = []
        raw_checks = reconciliation.get("checks") if isinstance(reconciliation.get("checks"), list) else []
        for item in raw_checks[:8]:
            if not isinstance(item, dict):
                continue
            field = _enum_or_none(item.get("field"), ALLOWED_CHECK_FIELDS)
            result = _enum_or_none(item.get("result"), ALLOWED_CHECK_RESULTS)
            explanation = _short_text(item.get("explanation"), 240)
            if field and result:
                checks.append({"field": field, "result": result, "explanation": explanation or "No additional explanation available."})

        timeline = []
        raw_timeline = facts.get("timeline") if isinstance(facts.get("timeline"), list) else []
        for event in raw_timeline[-30:]:
            if not isinstance(event, dict):
                continue
            event_type = _enum_or_none(event.get("event_type"), ALLOWED_EVENT_TYPES)
            if not event_type:
                continue
            item = {
                "event_type": event_type,
                "description": _short_text(event.get("description"), 240) or "A lifecycle event was recorded.",
            }
            round_value = event.get("round")
            if isinstance(round_value, int) and not isinstance(round_value, bool) and round_value >= 0:
                item["round"] = round_value
            timestamp = _short_text(event.get("timestamp"), 40)
            if timestamp:
                item["timestamp"] = timestamp
            # Do not pass transaction refs, device IDs, hashes, or arbitrary metadata.
            timeline.append(item)

        raw_sources = facts.get("sources") if isinstance(facts.get("sources"), dict) else {}
        sources = {}
        for key in (
            "current_chain_state",
            "independent_chain_history",
            "database_record",
            "database_event_mirror",
        ):
            value = _enum_or_none(raw_sources.get(key), ALLOWED_SOURCE_VALUES)
            if value:
                sources[key] = value
        for key in ("history_from_round", "indexer_through_round", "algod_head_round", "transactions_scanned", "pages_scanned"):
            value = raw_sources.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                sources[key] = value

        raw_limitations = facts.get("limitations") if isinstance(facts.get("limitations"), list) else []
        limitations = []
        for item in raw_limitations[:8]:
            safe_note = _short_text(item, 240)
            if safe_note:
                limitations.append(safe_note)

        return {
            "deterministic_summary": _short_text(facts.get("summary"), 600) or "No deterministic summary is available.",
            "reconciliation_status": status,
            "reconciliation_headline": headline,
            "checks": checks,
            "timeline": timeline,
            "sources": sources,
            "limitations": limitations,
        }

    @staticmethod
    def _fallback(facts: Dict[str, Any], reason: str) -> Dict[str, Any]:
        summary = _short_text(facts.get("summary"), 1000) if isinstance(facts, dict) else None
        reconciliation = facts.get("reconciliation") if isinstance(facts, dict) else None
        headline = _short_text(reconciliation.get("headline"), 240) if isinstance(reconciliation, dict) else None
        if summary and headline and headline not in summary:
            text = f"{headline} {summary}"
        else:
            text = summary or headline or "The passport explanation is not available right now."
        return {
            "provider": "deterministic_fallback",
            "generated": False,
            "model": None,
            "text": text,
            "fallback_reason": reason,
        }
