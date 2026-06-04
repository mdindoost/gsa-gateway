"""Abstract base class for all platform connectors."""

from abc import ABC, abstractmethod


class BasePlatform(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def setup_services(self) -> None: ...
