from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse


@dataclass(frozen=True)
class Company:
    name: str
    ticker: str
    profile_url: str
    market: str = ""  # human-readable label (e.g. "KSA", "Qatar") - display only, not used for routing

    @property
    def exchange_host(self) -> str:
        return urlparse(self.profile_url).netloc.lower()


@dataclass(frozen=True)
class Announcement:
    id: str
    company: Company
    title: str
    date_text: str
    detail_url: str
    # Which of an exchange's feeds this came from. Empty for a scraper's
    # original feed; a named source is new for a company the first time it
    # appears, and then only its recent items alert (see run.py).
    source: str = ""
    # When the exchange published it, if the feed says (timezone-aware).
    published: datetime | None = None
