import json
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

    def record(self, ticker: str, current_ids: list[str]) -> None:
        entry = self._data.setdefault(ticker, {"seeded": False, "seen_ids": []})
        merged = set(entry["seen_ids"]) | set(current_ids)
        entry["seen_ids"] = sorted(merged)
        entry["seeded"] = True
        entry.pop("consecutive_failures", None)  # a successful check ends any failure streak

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
