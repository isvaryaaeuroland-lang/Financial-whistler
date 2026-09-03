from dataclasses import dataclass
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
