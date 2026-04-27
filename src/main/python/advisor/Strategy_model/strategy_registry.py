from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from advisor.core.locks import CONFIG_LOCK, STRATEGY_REGISTRY_LOCK
from advisor.utils.logging_setup import get_logger

logger = get_logger("StrategyRegistry")


class StrategyRegistry:
    """
    Central registry for strategy definitions and runtime telemetry.

    This registry stores:
    - strategy configs (from configs.json)
    - symbol attachments
    - live signal counters
    - backtest outcomes
    - per-strategy error history
    """

    VERSION = "1.0"

    def __init__(
        self,
        root_path: Path | None = None,
        persist_interval_seconds: float = 2.0,
        max_events: int = 120,
    ):
        self.root_path = Path(root_path) if root_path else Path(__file__).resolve().parents[5]
        self.config_path = self.root_path / "configs.json"
        self.runtime_dir = self.root_path / "runtime"
        self.registry_path = self.runtime_dir / "strategy_registry.json"

        self.persist_interval_seconds = max(float(persist_interval_seconds), 0.0)
        self.max_events = max(20, int(max_events))

        self._lock = RLock()
        self._last_persist_monotonic = 0.0
        self._store = self._load_registry_store()

    # -----------------------------------------------------
    # Public API
    # -----------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._store)

    def list_strategies(self) -> list[dict[str, Any]]:
        with self._lock:
            rows: list[dict[str, Any]] = []
            for entry in self._store.get("strategies", {}).values():
                rows.append(
                    {
                        "name": entry.get("name"),
                        "key": entry.get("key"),
                        "updated_at": entry.get("updated_at"),
                        "stats": copy.deepcopy(entry.get("stats", {})),
                        "symbols": sorted(list((entry.get("symbols") or {}).keys())),
                    }
                )
            rows.sort(key=lambda item: str(item.get("name") or "").casefold())
            return rows

    def get_config(self, strategy_name: str | None) -> dict[str, Any] | None:
        key = self._strategy_key(strategy_name)
        if not key:
            return None
        with self._lock:
            entry = self._store.get("strategies", {}).get(key)
            if isinstance(entry, dict):
                cfg = entry.get("config")
                if isinstance(cfg, dict):
                    return copy.deepcopy(cfg)

        self.refresh_configs(persist=True)
        with self._lock:
            entry = self._store.get("strategies", {}).get(key)
            if isinstance(entry, dict):
                cfg = entry.get("config")
                if isinstance(cfg, dict):
                    return copy.deepcopy(cfg)
        return None

    def refresh_configs(self, persist: bool = True) -> dict[str, dict[str, Any]]:
        """
        Reconcile strategy config definitions from configs.json into registry store.
        """
        config_doc = self._load_configs()
        raw = config_doc.get("strategies") if isinstance(config_doc, dict) else {}
        if not isinstance(raw, dict):
            raw = {}

        now = self._now_iso()
        with self._lock:
            catalog: dict[str, dict[str, Any]] = {}
            for raw_name, config in raw.items():
                if not isinstance(config, dict):
                    continue
                name = str(config.get("name") or raw_name or "").strip()
                if not name:
                    continue
                key = self._strategy_key(name)
                if not key:
                    continue

                entry = self._ensure_strategy_locked(name=name, config=config, source="config_refresh")
                entry["updated_at"] = now
                catalog[key] = copy.deepcopy(config)

            self._touch_store_locked(now)
            if persist:
                self._save_registry_locked(force=False)
            return catalog

    def upsert_config(self, name: str, config: dict[str, Any], overwrite: bool = True) -> tuple[bool, dict[str, Any]]:
        """
        Upsert strategy config into configs.json and mirror to registry.
        Returns tuple: (already_existed, compiled_config)
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("strategy name is required")
        if not isinstance(config, dict):
            raise ValueError("strategy config must be a dict")

        with CONFIG_LOCK:
            doc = self._safe_load_json(self.config_path)
            strategies = doc.get("strategies")
            if not isinstance(strategies, dict):
                strategies = {}

            existed = clean_name in strategies
            if existed and not overwrite:
                raise ValueError(f"strategy already exists: {clean_name}")

            stored = copy.deepcopy(config)
            stored["name"] = clean_name
            strategies[clean_name] = stored
            doc["strategies"] = strategies
            self._safe_write_json(self.config_path, doc)

        with self._lock:
            self._ensure_strategy_locked(clean_name, stored, source="config_upsert")
            self._append_event_locked(
                self._strategy_key(clean_name),
                {
                    "timestamp": self._now_iso(),
                    "kind": "config_upsert",
                    "source": "api",
                    "overwrite": bool(existed),
                },
            )
            self._touch_store_locked()
            self._save_registry_locked(force=True)

        return existed, stored

    def record_attach(self, symbol: str, strategy_name: str, config: dict[str, Any] | None = None, source: str = "runtime") -> None:
        clean_symbol = str(symbol or "").strip()
        clean_name = str(strategy_name or "").strip()
        if not clean_symbol or not clean_name:
            return

        key = self._strategy_key(clean_name)
        now = self._now_iso()
        with self._lock:
            entry = self._ensure_strategy_locked(clean_name, config=config, source=source)
            sym = self._ensure_symbol_metrics_locked(entry, clean_symbol)
            if not sym.get("attached_at"):
                sym["attached_at"] = now
            sym["last_seen_at"] = now
            sym["last_mode"] = "live"
            self._bump_stat_locked(entry, "attach_count", 1)
            entry["stats"]["last_attach_at"] = now
            self._append_event_locked(
                key,
                {
                    "timestamp": now,
                    "kind": "attach",
                    "symbol": clean_symbol,
                    "source": source,
                },
            )
            self._touch_store_locked(now)
            self._save_registry_locked(force=False)

    def record_signal(self, symbol: str, strategy_name: str, payload: dict[str, Any] | None = None) -> None:
        clean_symbol = str(symbol or "").strip()
        clean_name = str(strategy_name or "").strip()
        if not clean_symbol or not clean_name:
            return

        now = self._now_iso()
        key = self._strategy_key(clean_name)
        score, confidence = self._extract_score_confidence(payload or {})

        with self._lock:
            entry = self._ensure_strategy_locked(clean_name, config=None, source="signal")
            sym = self._ensure_symbol_metrics_locked(entry, clean_symbol)
            sym["signal_count"] = int(sym.get("signal_count", 0)) + 1
            sym["last_signal_at"] = now
            sym["last_seen_at"] = now
            sym["last_mode"] = "live"
            if score is not None:
                sym["last_score"] = score
            if confidence is not None:
                sym["last_confidence"] = confidence

            self._bump_stat_locked(entry, "signal_count", 1)
            entry["stats"]["last_signal_at"] = now
            if score is not None:
                entry["stats"]["last_score"] = score
            if confidence is not None:
                entry["stats"]["last_confidence"] = confidence

            self._append_event_locked(
                key,
                {
                    "timestamp": now,
                    "kind": "signal",
                    "symbol": clean_symbol,
                    "score": score,
                    "confidence": confidence,
                    "direction": self._extract_direction(payload or {}),
                },
            )
            self._touch_store_locked(now)
            self._save_registry_locked(force=False)

    def record_backtest(
        self,
        symbol: str,
        strategy_name: str,
        ok: bool,
        payload: dict[str, Any] | None = None,
        request_payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        clean_symbol = str(symbol or "").strip()
        clean_name = str(strategy_name or "").strip()
        if not clean_symbol or not clean_name:
            return

        key = self._strategy_key(clean_name)
        now = self._now_iso()
        score, confidence = self._extract_score_confidence(payload or {})

        with self._lock:
            entry = self._ensure_strategy_locked(clean_name, config=None, source="backtest")
            sym = self._ensure_symbol_metrics_locked(entry, clean_symbol)
            sym["backtest_count"] = int(sym.get("backtest_count", 0)) + 1
            sym["last_backtest_at"] = now
            sym["last_seen_at"] = now
            sym["last_mode"] = "backtest"
            sym["last_backtest_ok"] = bool(ok)
            if ok:
                sym["pass_count"] = int(sym.get("pass_count", 0)) + 1
            else:
                sym["fail_count"] = int(sym.get("fail_count", 0)) + 1
            if score is not None:
                sym["last_score"] = score
            if confidence is not None:
                sym["last_confidence"] = confidence
            if error:
                sym["last_error"] = str(error)
                sym["last_error_at"] = now

            self._bump_stat_locked(entry, "backtest_count", 1)
            self._bump_stat_locked(entry, "pass_count" if ok else "fail_count", 1)
            entry["stats"]["last_backtest_at"] = now
            entry["stats"]["last_backtest_ok"] = bool(ok)
            if score is not None:
                entry["stats"]["last_score"] = score
            if confidence is not None:
                entry["stats"]["last_confidence"] = confidence
            if error:
                self._bump_stat_locked(entry, "error_count", 1)
                entry["stats"]["last_error_at"] = now

            event = {
                "timestamp": now,
                "kind": "backtest",
                "symbol": clean_symbol,
                "ok": bool(ok),
                "score": score,
                "confidence": confidence,
            }
            if request_payload:
                event["request"] = self._json_safe(request_payload)
            if error:
                event["error"] = str(error)
            self._append_event_locked(key, event)
            self._touch_store_locked(now)
            self._save_registry_locked(force=True)

    def record_error(self, symbol: str, strategy_name: str, error: str, phase: str = "runtime") -> None:
        clean_symbol = str(symbol or "").strip()
        clean_name = str(strategy_name or "").strip()
        if not clean_symbol or not clean_name:
            return

        now = self._now_iso()
        key = self._strategy_key(clean_name)
        with self._lock:
            entry = self._ensure_strategy_locked(clean_name, config=None, source=phase)
            sym = self._ensure_symbol_metrics_locked(entry, clean_symbol)
            sym["last_error"] = str(error)
            sym["last_error_at"] = now
            sym["last_seen_at"] = now
            self._bump_stat_locked(entry, "error_count", 1)
            entry["stats"]["last_error_at"] = now
            self._append_event_locked(
                key,
                {
                    "timestamp": now,
                    "kind": "error",
                    "symbol": clean_symbol,
                    "phase": phase,
                    "error": str(error),
                },
            )
            self._touch_store_locked(now)
            self._save_registry_locked(force=True)

    # -----------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------

    def _load_registry_store(self) -> dict[str, Any]:
        with STRATEGY_REGISTRY_LOCK:
            data = self._safe_load_json(self.registry_path)
        if not data:
            data = {}
        if not isinstance(data.get("strategies"), dict):
            data["strategies"] = {}
        data.setdefault("version", self.VERSION)
        data.setdefault("updated_at", None)
        data.setdefault("config_path", str(self.config_path))
        data.setdefault("registry_path", str(self.registry_path))
        return data

    def _ensure_strategy_locked(
        self,
        name: str,
        config: dict[str, Any] | None = None,
        source: str = "runtime",
    ) -> dict[str, Any]:
        key = self._strategy_key(name)
        now = self._now_iso()
        if not key:
            raise ValueError("strategy name must not be empty")

        strategies = self._store.setdefault("strategies", {})
        entry = strategies.get(key)
        if not isinstance(entry, dict):
            entry = {
                "key": key,
                "name": str(name).strip(),
                "source": source,
                "created_at": now,
                "updated_at": now,
                "config": {},
                "symbols": {},
                "stats": {
                    "attach_count": 0,
                    "signal_count": 0,
                    "backtest_count": 0,
                    "pass_count": 0,
                    "fail_count": 0,
                    "error_count": 0,
                    "last_attach_at": None,
                    "last_signal_at": None,
                    "last_backtest_at": None,
                    "last_backtest_ok": None,
                    "last_error_at": None,
                    "last_score": None,
                    "last_confidence": None,
                },
                "events": [],
            }
            strategies[key] = entry

        entry["name"] = str(name).strip()
        entry["source"] = source
        entry["updated_at"] = now
        if isinstance(config, dict) and config:
            entry["config"] = copy.deepcopy(config)
        elif not isinstance(entry.get("config"), dict):
            entry["config"] = {}
        if not isinstance(entry.get("symbols"), dict):
            entry["symbols"] = {}
        if not isinstance(entry.get("stats"), dict):
            entry["stats"] = {}
        if not isinstance(entry.get("events"), list):
            entry["events"] = []
        return entry

    def _ensure_symbol_metrics_locked(self, entry: dict[str, Any], symbol: str) -> dict[str, Any]:
        symbols = entry.setdefault("symbols", {})
        metrics = symbols.get(symbol)
        if not isinstance(metrics, dict):
            metrics = {
                "attached_at": None,
                "last_seen_at": None,
                "last_mode": None,
                "signal_count": 0,
                "backtest_count": 0,
                "pass_count": 0,
                "fail_count": 0,
                "last_signal_at": None,
                "last_backtest_at": None,
                "last_backtest_ok": None,
                "last_score": None,
                "last_confidence": None,
                "last_error": None,
                "last_error_at": None,
            }
            symbols[symbol] = metrics
        return metrics

    @staticmethod
    def _extract_direction(payload: dict[str, Any]) -> str | None:
        for key in ("side", "direction"):
            value = payload.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        data = payload.get("data")
        if isinstance(data, dict):
            value = data.get("direction")
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        return None

    @staticmethod
    def _extract_score_confidence(payload: dict[str, Any]) -> tuple[float | None, float | None]:
        score = None
        confidence = None
        data = payload.get("data")
        if isinstance(data, dict):
            score = StrategyRegistry._safe_float(data.get("score"))
            confidence = StrategyRegistry._safe_float(data.get("confidence"))
        if score is None:
            score = StrategyRegistry._safe_float(payload.get("score"))
        if confidence is None:
            confidence = StrategyRegistry._safe_float(payload.get("confidence"))
        return score, confidence

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            if isinstance(value, str) and not value.strip():
                return None
            return float(value)
        except Exception:
            return None

    def _append_event_locked(self, strategy_key: str, event: dict[str, Any]) -> None:
        entry = self._store.get("strategies", {}).get(strategy_key)
        if not isinstance(entry, dict):
            return
        events = entry.setdefault("events", [])
        events.append(self._json_safe(event))
        if len(events) > self.max_events:
            del events[:-self.max_events]

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): StrategyRegistry._json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [StrategyRegistry._json_safe(v) for v in value]
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    @staticmethod
    def _strategy_key(name: str | None) -> str:
        text = str(name or "").strip().casefold()
        return "".join(ch for ch in text if ch.isalnum())

    def _touch_store_locked(self, now: str | None = None) -> None:
        self._store["updated_at"] = now or self._now_iso()

    @staticmethod
    def _bump_stat_locked(entry: dict[str, Any], key: str, amount: int) -> None:
        stats = entry.setdefault("stats", {})
        stats[key] = int(stats.get(key, 0)) + int(amount)

    def _save_registry_locked(self, force: bool) -> None:
        now = time.monotonic()
        if not force and self.persist_interval_seconds > 0:
            if (now - self._last_persist_monotonic) < self.persist_interval_seconds:
                return
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        payload = copy.deepcopy(self._store)
        with STRATEGY_REGISTRY_LOCK:
            self._safe_write_json(self.registry_path, payload)
        self._last_persist_monotonic = now

    def _load_configs(self) -> dict[str, Any]:
        with CONFIG_LOCK:
            return self._safe_load_json(self.config_path)

    @staticmethod
    def _safe_load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                return payload
            return {}
        except Exception:
            logger.exception("Failed to read JSON from %s", path)
            return {}

    @staticmethod
    def _safe_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        tmp.replace(path)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
