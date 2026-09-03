from abc import ABC, abstractmethod

from ..models import Announcement, Company


class ExchangeScraper(ABC):
    """One instance handles every company listed on a given exchange for a
    single run, so the underlying browser/session can be reused instead of
    paying startup cost per company."""

    exchange_name: str

    @abstractmethod
    def run(self, companies: list[Company]) -> dict[str, list[Announcement] | None]:
        """Returns {ticker: [Announcement, ...]}. A value of None means the
        fetch failed for that company (e.g. blocked/timeout) and should be
        skipped this run rather than treated as "no announcements"."""
        raise NotImplementedError
