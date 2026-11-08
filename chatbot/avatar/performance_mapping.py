"""Only verified Chatbot-side pauses are mapped; Qwen clones stay unchanged."""
from dataclasses import dataclass, asdict
from chatbot.core.performance import Performance, PerformanceCapabilities


@dataclass(frozen=True, slots=True)
class DeliveryControls:
    pause_seconds: float | None = None


class NullMapper:
    def capabilities(self):
        return PerformanceCapabilities()

    def map(self, performance):
        return DeliveryControls()


class SpeechDeliveryMapper(NullMapper):
    def capabilities(self):
        return PerformanceCapabilities(('pause',))

    def map(self, performance: Performance | None):
        return DeliveryControls({'short': .15, 'long': .4}.get(performance.pause)) if performance else DeliveryControls()


def map_delivery(performance, mapper=None):
    mapper = mapper or NullMapper()
    supported = mapper.capabilities().controls
    ignored = [key for key, value in asdict(performance).items() if value and key not in supported] if performance else []
    try:
        return mapper.map(performance), ignored
    except Exception:
        return DeliveryControls(), ignored + ['mapper_error']
