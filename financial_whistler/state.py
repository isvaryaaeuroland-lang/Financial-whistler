import json
from datetime import date
from pathlib import Path


class StateStore:
    """Tracks which announcement IDs have already been seen per ticker, so we
    only alert once per announcement and never re-alert across runs.

    First time a ticker is seen, its current announcements are recorded as a
    baseline without triggering alerts -- otherwise every company's entire
    visible announcement history would fire an alert on the very first run.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def is_seeded(self, ticker: str) -> bool:
        return self._data.get(ticker, {}).get("seeded", False)

    def seen_ids(self, ticker: str) -> set[str]:
        return set(self._data.get(ticker, {}).get("seen_ids", []))

    def record(self, ticker: str, current_ids: list[str], today: date | None = None) -> None:
        entry = self._data.setdefault(ticker, {"seeded": False, "seen_ids": []})
        before = set(entry["seen_ids"])
        merged = before | set(current_ids)
        entry["seen_ids"] = sorted(merged)
        entry["seeded"] = True
        entry.pop("consecutive_failures", None)  # a successful check ends any failure streak
        # When this company last showed anything new -- the daily status
        # flags companies quiet for too long, which is how a feed that has
        # silently stopped updating (NSE's, Jan 2025 - Oct 2026) gets noticed.
        today = today or date.today()
        if merged != before or "last_new_on" not in entry:
            entry["last_new_on"] = today.isoformat()

    def last_new_on(self, ticker: str) -> date | None:
        value = self._data.get(ticker, {}).get("last_new_on")
        return date.fromisoformat(value) if value else None

    def is_source_seeded(self, ticker: str, source: str) -> bool:
        return source in self._data.get(ticker, {}).get("seeded_sources", [])

    def mark_sources_seeded(self, ticker: str, sources: set[str]) -> None:
        entry = self._data.setdefault(ticker, {"seeded": False, "seen_ids": []})
        entry["seeded_sources"] = sorted(set(entry.get("seeded_sources", [])) | sources)

    def daily_log(self) -> dict:
        """Today's run summaries for the daily status email, kept in the same
        file under a key no ticker can collide with. Shape is owned by
        daily_status.py."""
        return self._data.setdefault("_daily_status", {})

    def record_failure(self, ticker: str) -> int:
        """Counts a failed check and returns how many runs in a row this
        ticker has now failed. Reset by the next successful `record`."""
        entry = self._data.setdefault(ticker, {"seeded": False, "seen_ids": []})
        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        return entry["consecutive_failures"]
