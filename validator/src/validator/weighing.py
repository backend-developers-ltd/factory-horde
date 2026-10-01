"""Stable softmax over durable accepted scores, with current-registration revalidation."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import exp, fsum, isfinite
from uuid import UUID

import structlog
from nexus.v1 import Hotkey, NexusException, Weight, WeightsCalculationBundle
from prometheus_client import Counter, Histogram
from pylon_client.artanis import Config, PylonClient
from pylon_client.artanis.v1 import GetNeuronsResponse

from validator.records import MinerHotkey, NonnegativeInt, Record, Score, UtcTime
from validator.result_store import FileTaskResultStore

_logger = structlog.get_logger(__name__)
_events = Counter("factory_horde_weighing_total", "Weight selection and calculation events", ("outcome",))
_latency = Histogram("factory_horde_weighing_seconds", "Weight calculation and registration verification duration")


class NoUsableScores(NexusException):
    """An expected gate closure, never an instruction to send an empty weight mapping."""


class RegistrationChanged(NexusException):
    """Registration changed during calculation; a later opportunity must recalculate."""


@dataclass(frozen=True)
class Membership:
    """A public Pylon v1 membership observation; hotkeys remain the scoring identities."""

    block: int
    uids: Mapping[Hotkey, int]

    @classmethod
    def from_response(cls, response: GetNeuronsResponse) -> Membership:
        """Require consistent hotkey attribution and unique UIDs.

        Raises:
            ValueError: Pylon membership is internally inconsistent.
        """
        if any(key != neuron.hotkey for key, neuron in response.neurons.items()):
            raise ValueError("Membership hotkey attribution mismatch")
        uids = {Hotkey(key): int(neuron.uid) for key, neuron in response.neurons.items()}
        if len(set(uids.values())) != len(uids):
            raise ValueError("Membership contains duplicate UIDs")
        return cls(response.block.number, uids)


def read_membership(config: Config, netuid: int) -> Membership:
    """Use the actor callback's public identity API; reject a changed subnet mapping.

    Raises:
        ValueError: The configured identity no longer selects this subnet.
    """
    with PylonClient(config) as client:
        response = client.v1.identity.get_latest_neurons()
        if client.v1.identity.netuid != netuid:
            raise ValueError("Pylon identity belongs to a different subnet")
        return Membership.from_response(response)


def softmax(scores: Mapping[Hotkey, float], temperature: float = 0.1) -> dict[Hotkey, Weight]:
    """Normalize successful scores without overflow or assigning values to failed miners.

    Raises:
        ValueError: Temperature or a score is invalid, or no eligible scores exist.
    """
    if not isfinite(temperature) or temperature <= 0:
        raise ValueError("Temperature must be finite and positive")
    if not scores or any(not isfinite(score) or not 0 <= score <= 1 for score in scores.values()):
        raise ValueError("Softmax requires nonempty finite scores in [0, 1]")
    maximum = max(scores.values())
    numerators = {key: exp((score - maximum) / temperature) for key, score in sorted(scores.items())}
    total = fsum(numerators.values())
    return {key: Weight(value / total) for key, value in numerators.items()}


class WeightCalculation(Record):
    """Latest calculated request, not an acknowledgement or evidence of on-chain inclusion."""

    round_id: UUID
    calculated_at: UtcTime
    membership_block: NonnegativeInt
    epoch_start: int
    epoch_end: int
    temperature: float
    uids: dict[MinerHotkey, NonnegativeInt]
    weights: dict[MinerHotkey, Score]


class Weigher:
    """Read the shared task provider's repository; chain epoch never expires application scores."""

    def __init__(self, store: FileTaskResultStore, membership: Callable[[], Membership], temperature: float = 0.1):
        if not isfinite(temperature) or temperature <= 0:
            raise ValueError("Temperature must be finite and positive")
        self.store, self.membership, self.temperature = store, membership, temperature

    def select(self) -> tuple[UUID, Membership, dict[Hotkey, float]]:
        """Select the latest completed nonempty round, then filter by currently registered hotkeys.

        Raises:
            NoUsableScores: No retained history or no remaining registered recipients.
        """
        usable = self.store.repository.latest_usable_round()
        if usable is None:
            raise NoUsableScores("No completed round with accepted scores")
        plan, accepted = usable
        current = self.membership()
        scores = {
            Hotkey(result.miner_hotkey): result.score for result in accepted if result.miner_hotkey in current.uids
        }
        if not scores:
            raise NoUsableScores("No accepted hotkeys remain registered")
        return plan.round_id, current, scores

    def calculate(self, bundle: WeightsCalculationBundle) -> Mapping[Hotkey, Weight]:
        """Recompute at the setter and revalidate membership immediately before returning.

        Raises:
            ValueError: The setter was wired to a different store.
            RegistrationChanged: Registration moved during calculation; no mapping is submitted.
        """
        with _latency.time():
            try:
                if bundle.tasks_result_store is not self.store:
                    raise ValueError("Weight setter must share the factory/evaluation result store")
                round_id, initial, scores = self.select()
                weights = softmax(scores, self.temperature)
                final = self.membership()
                if dict(initial.uids) != dict(final.uids):
                    raise RegistrationChanged("Membership changed during weight calculation")
                self.store.files.replace(
                    "control/weight-calculation.json",
                    WeightCalculation(
                        round_id=round_id,
                        calculated_at=datetime.now(UTC),
                        membership_block=final.block,
                        epoch_start=bundle.epoch.first_block,
                        epoch_end=bundle.epoch.last_block,
                        temperature=self.temperature,
                        uids={key: final.uids[key] for key in weights},
                        weights={str(key): float(value) for key, value in weights.items()},
                    ),
                )
            except Exception:
                _events.labels("error").inc()
                raise
            _events.labels("calculated").inc()
            _logger.info(
                "weights_calculated",
                round_id=str(round_id),
                recipients=len(weights),
                membership_block=final.block,
                temperature=self.temperature,
            )
            return weights
