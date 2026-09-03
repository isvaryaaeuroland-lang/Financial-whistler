from abc import ABC, abstractmethod

from ..models import Announcement


class Alerter(ABC):
    @abstractmethod
    def send_digest(self, announcements: list[Announcement]) -> None:
        raise NotImplementedError
