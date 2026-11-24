"""Ports of the core (Ports and Adapters, ADR-001).

The core defines these interfaces; adapters implement them. The core never
imports an adapter.
"""
from abc import ABC, abstractmethod
from typing import List, Optional

from .model import Classification, Direction, Signal, ThrowEvent


class ClassifierPort(ABC):
    @abstractmethod
    def classify(self, image: bytes) -> Classification:
        """Return the detected fraction (or None = UNBEKANNT) and confidence."""


class ModulePort(ABC):
    """Commands from the core to the ESP32 modules."""

    @abstractmethod
    def send_config(self, module_id: int) -> None: ...

    @abstractmethod
    def send_signal(self, module_id: int, seq: int, signal: Signal) -> None: ...

    @abstractmethod
    def send_sort(self, module_id: int, seq: int, direction: Direction,
                  signal: Optional[Signal] = None) -> None:
        """`signal` repeats the LED pattern so a lost SIGNAL frame cannot
        leave the user without feedback."""

    @abstractmethod
    def send_heartbeat(self, module_id: int) -> None: ...

    @abstractmethod
    def send_reset_fault(self, module_id: int) -> None: ...


class EventStorePort(ABC):
    @abstractmethod
    def save(self, event: ThrowEvent) -> None: ...

    @abstractmethod
    def all(self) -> List[ThrowEvent]: ...
