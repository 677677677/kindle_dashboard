"""保存仓库外按日候选汇总；没有全天覆盖证据时，趋势明确留空。"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Any

from .core import statistical_date_for, statistical_day_bounds
from .core.allocation import ALLOCATION_VERSION


def _document(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= 86400 else None


def update_history(
    root: Path, now: datetime, records: dict[str, dict],
    writer: Callable[[Path, str], None], rule_fingerprint: str = "",
) -> dict:
    target = root / "history" / "daily-summary.json"
    cached = _document(target)
    compatible = (cached.get("algorithmVersion") == ALLOCATION_VERSION
                  and cached.get("ruleFingerprint") == rule_fingerprint
                  and cached.get("timezone") == str(now.tzinfo))
    days = cached.get("days", {}) if compatible else {}
    days = days if isinstance(days, dict) else {}
    for date, record in records.items():
        if not isinstance(record, dict):
            continue
        previous = days.get(date, {})
        previous = previous if isinstance(previous, dict) else {}
        updated = {**previous, "plan": record.get("plan", previous.get("plan", {}))}
        available = record.get("inputsAvailable", {})
        previous_available = previous.get("inputsAvailable", {})
        available = available if isinstance(available, dict) else {}
        previous_available = previous_available if isinstance(previous_available, dict) else {}
        updated["inputsAvailable"] = {key: bool(available.get(key) or previous_available.get(key))
                                      for key in ("phone", "computer", "koreader")}
        for key in ("phoneSeconds", "readingSeconds"):
            value = _seconds(record.get(key))
            updated[key] = value if value is not None else _seconds(previous.get(key))
            if value is not None:
                updated["generatedAt"] = now.isoformat()
        days[date] = updated
    today = statistical_date_for(now)
    retain = {(today - timedelta(days=index)).isoformat() for index in range(30)}
    days = {date: value for date, value in days.items() if date in retain and isinstance(value, dict)}
    coverage = _document(root / "history" / "coverage.json")
    proofs = coverage.get("days", {}) if coverage.get("schemaVersion") == "history-coverage-v1" else {}
    result = _public_days(now, days, proofs if isinstance(proofs, dict) else {})
    payload = {"schemaVersion": "daily-history-v1", "algorithmVersion": ALLOCATION_VERSION,
               "ruleFingerprint": rule_fingerprint, "timezone": str(now.tzinfo), "days": days}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        writer(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        # 缓存写入失败时，仍可展示本次读取到的候选数据。
        pass
    return {"version": "history-v1", "days": result}


def _public_days(now: datetime, records: dict, proofs: dict) -> list[dict]:
    today = statistical_date_for(now)
    result = []
    for index in range(7, 0, -1):
        date = today - timedelta(days=index)
        day = statistical_day_bounds(date, str(now.tzinfo))
        record = records.get(date.isoformat(), {})
        record = record if isinstance(record, dict) else {}
        proof = proofs.get(date.isoformat(), {})
        proof = proof if isinstance(proof, dict) else {}
        available = record.get("inputsAvailable", {})
        available = available if isinstance(available, dict) else {}
        complete = {source: available.get(source) is True and _covers(proof.get(source), day)
                    for source in ("phone", "computer", "koreader")}
        phone = _visible_seconds(record.get("phoneSeconds"), complete["phone"])
        reading_complete = all(complete.values())
        reading = _visible_seconds(record.get("readingSeconds"), reading_complete)
        has_candidate = any(_seconds(record.get(key)) is not None for key in ("phoneSeconds", "readingSeconds"))
        status = "complete" if complete["phone"] and reading_complete and phone is not None and reading is not None else "partial" if has_candidate else "missing"
        result.append({"date": date.isoformat(), "phoneMinutes": _minutes(phone),
                       "readingMinutes": _minutes(reading), "phonePercent": _percent(phone),
                       "readingPercent": _percent(reading), "status": status,
                       "phoneComplete": complete["phone"], "readingComplete": reading_complete})
    return result


def _visible_seconds(value: Any, complete: bool) -> float | None:
    seconds = _seconds(value)
    return seconds if complete or (seconds is not None and seconds > 0) else None


def _covers(proof: Any, day) -> bool:
    if not isinstance(proof, dict) or proof.get("method") not in {"user-confirmed", "collector-attested"}:
        return False
    try:
        start = datetime.fromisoformat(proof["startAt"])
        end = datetime.fromisoformat(proof["endAt"])
        return start.tzinfo is not None and end.tzinfo is not None and start <= day.start and end >= day.end
    except (KeyError, TypeError, ValueError):
        return False


def _minutes(seconds: float | None) -> float | None:
    return round(seconds / 60, 2) if seconds is not None else None


def _percent(seconds: float | None) -> float | None:
    return round(seconds / 86400 * 100, 4) if seconds is not None else None

