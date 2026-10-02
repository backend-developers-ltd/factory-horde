"""FactoryHorde Nexus runtime: persisted round coordination and two file-backed tasks."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Self

import click
import sentry_sdk
from dotenv import load_dotenv
from nexus.v1 import (
    BlockCount,
    MechanismId,
    NetUid,
    NexusValidator,
    PylonClientSettingsMixin,
    SetWeightsBeatNode,
    Tempo,
    WeightSetterNode,
    WeightSettingSuccess,
)
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pylon_client.artanis import Config, IdentityName, PylonAuthToken
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.litestar import LitestarIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration

from validator.chain_observer import ChainObservation, ChainObserverNode
from validator.coordinator import RoundCoordinator, RoundTick, RoundTiming
from validator.discovery import freeze_via_pylon
from validator.health import Readiness
from validator.logging_config import LoggingSettings, configure_logging
from validator.monitoring import MonitoringNode
from validator.otel import OtelSettings, setup_otel
from validator.records import ImageReference, MinerHotkey
from validator.response_logger import ErrorLoggerNode, MessageLoggerNode
from validator.result_repository import ResultRepository
from validator.round_actor import RoundCoordinatorNode
from validator.round_repository import RoundRepository
from validator.tasks import FileTasks
from validator.weighing import Weigher, read_membership
from validator.weight_gate import WeightGate


class Settings(PylonClientSettingsMixin, BaseSettings):
    """Explicit independent configuration for round dispatch and chain weight writes."""

    model_config = SettingsConfigDict(env_prefix="VALIDATOR_", extra="ignore")

    netuid: int = Field(validation_alias=AliasChoices("VALIDATOR_NETUID", "NETUID"))
    mechanism_id: MechanismId = Field(default=MechanismId(0), ge=0, validation_alias="MECHANISM_ID")
    data_root: Path = Path("/var/lib/factory-horde")
    dispatch_enabled: bool = False
    validator_hotkey: MinerHotkey | None = Field(default=None, validation_alias="VALIDATOR_HOTKEY")
    judge_image: ImageReference | None = None
    generation_window: timedelta = timedelta(hours=1)
    confirmation_window: timedelta = timedelta(minutes=5)
    evaluation_window: timedelta = timedelta(minutes=55)
    judge_stop_reserve: timedelta = timedelta(minutes=5)
    stop_grace_seconds: int = Field(default=60, ge=0, le=60)
    weights_enabled: bool = False
    weight_temperature: float = Field(default=0.1, gt=0, allow_inf_nan=False)
    weight_tempo: int = Field(default=360, gt=0)
    weight_epoch_offset: int = Field(default=0, ge=0)
    metrics_host: str = "0.0.0.0"
    metrics_port: int = Field(default=9101, ge=1, le=65535)
    observation_max_age_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)

    @field_validator("validator_hotkey", "judge_image", mode="before")
    @classmethod
    def _empty_optional(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator(
        "generation_window", "confirmation_window", "evaluation_window", "judge_stop_reserve", mode="before"
    )
    @classmethod
    def _seconds_or_duration(cls, value: object) -> object:
        if isinstance(value, str) and value.isdecimal():
            return timedelta(seconds=int(value))
        return value

    def round_timing(self) -> RoundTiming:
        """Validate and freeze the configured application timing policy."""
        return RoundTiming(
            generation=self.generation_window,
            confirmation=self.confirmation_window,
            evaluation=self.evaluation_window,
            judge_reserve=self.judge_stop_reserve,
            stop_grace_seconds=self.stop_grace_seconds,
        )

    @model_validator(mode="after")
    def _dispatch_configuration(self) -> Self:
        self.round_timing()
        if self.dispatch_enabled and not (
            self.validator_hotkey and self.judge_image and self.pylon_identity_name and self.pylon_identity_token
        ):
            raise ValueError("Dispatch requires validator hotkey, judge digest and Pylon identity credentials")
        if self.weights_enabled and not (self.pylon_identity_name and self.pylon_identity_token):
            raise ValueError("Weight setting requires Pylon identity credentials")
        if self.weight_epoch_offset > self.weight_tempo:
            raise ValueError("Weight epoch offset must fit within the configured tempo")
        return self

    def pylon_config(self) -> Config:
        """Build public-client configuration without exposing wallet material to the validator."""
        return Config(
            address=self.pylon_service_address,
            identity_name=IdentityName(self.pylon_identity_name) if self.pylon_identity_name is not None else None,
            identity_token=PylonAuthToken(self.pylon_identity_token) if self.pylon_identity_token is not None else None,
            open_access_token=PylonAuthToken(self.pylon_open_access_token),
        )


class Validator(NexusValidator):
    """Containerized Nexus runtime; all scheduling runs on its coordinator actor."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        observer = ChainObserverNode(settings.data_root, settings.mechanism_id, settings.dispatch_enabled)
        errors = ErrorLoggerNode("factory-horde-errors")
        observed = MessageLoggerNode[ChainObservation]("factory-horde-observed")
        self.connect(self.subnet_clock.source, observer.sink)
        self.connect(observer.error, errors.sink)
        self.connect(observer.ok, observed.sink)
        self.results = ResultRepository(RoundRepository(settings.data_root))
        self.tasks = FileTasks(self.results)
        self.tasks.connect(self, errors)
        monitoring = MonitoringNode(
            self.results,
            settings.dispatch_enabled,
            settings.metrics_host,
            settings.metrics_port,
            settings.observation_max_age_seconds,
        )
        self.connect(self.tasks.poll_clock.source, taps=(monitoring.sink,))
        self.connect(self.subnet_clock.source, taps=(monitoring.block,))
        self.connect(monitoring.error, errors.sink)
        self.connect(monitoring.ok, MessageLoggerNode[Readiness]("factory-horde-health").sink)
        if settings.dispatch_enabled and settings.validator_hotkey is not None and settings.judge_image is not None:
            discovery = partial(
                freeze_via_pylon,
                self.results.files,
                settings.pylon_config(),
                settings.netuid,
                settings.validator_hotkey,
            )
            coordinator = RoundCoordinatorNode(
                RoundCoordinator(self.results, settings.round_timing(), settings.judge_image, discovery)
            )
            self.connect(self.tasks.poll_clock.source, coordinator.sink)
            self.connect(self.subnet_clock.source, taps=(coordinator.block_beat,))
            self.connect(coordinator.factory, self.tasks.factory.input)
            self.connect(coordinator.evaluation, self.tasks.evaluation.input)
            self.connect(coordinator.error, errors.sink)
            self.connect(coordinator.error, taps=(monitoring.failure,))
            self.connect(coordinator.ok, taps=(monitoring.round,))
            self.connect(coordinator.ok, taps=(MessageLoggerNode[RoundTick]("factory-horde-rounds").sink,))
        if settings.weights_enabled:
            weigher = Weigher(
                self.tasks.store_provider.get_task_result_store(),
                partial(read_membership, settings.pylon_config(), settings.netuid),
                settings.weight_temperature,
            )
            opportunity = SetWeightsBeatNode(
                "factory-horde-weight-opportunities",
                netuid=NetUid(settings.netuid),
                epoch_start_offset=BlockCount(settings.weight_epoch_offset),
                mechanism_id=settings.mechanism_id,
                tempo=Tempo(settings.weight_tempo),
            )
            gate = WeightGate(weigher)
            setter = WeightSetterNode(
                "factory-horde-weight-setter",
                weighing_func=weigher.calculate,
                mechanism_id=settings.mechanism_id,
                task_result_store_provider=self.tasks.store_provider,
            )
            self.connect(self.subnet_clock.source, taps=(opportunity.block_beat,))
            self.connect(opportunity.source, gate.sink)
            self.connect(gate.ok, setter.sink)
            self.connect(gate.error, errors.sink)
            self.connect(setter.error, errors.sink)
            self.connect(
                setter.ok,
                taps=(
                    MessageLoggerNode[WeightSettingSuccess]("factory-horde-weight-requests").sink,
                    monitoring.submission,
                ),
            )


def _setup_sentry() -> None:
    # Optional Sentry integration. Enable by setting the SENTRY_DSN env var.
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            LitestarIntegration(),
            LoggingIntegration(event_level=logging.ERROR),
            HttpxIntegration(),
            ThreadingIntegration(propagate_scope=True),
        ],
    )


@click.command()
@click.option("--env-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
def main(env_file: Path | None) -> None:
    """CLI entry point: load env from --env-file (if given) and run the validator."""
    load_dotenv(env_file)
    logging_settings = LoggingSettings()
    configure_logging(logging_settings)
    setup_otel(OtelSettings())
    _setup_sentry()
    Validator.run(settings_class=Settings)


if __name__ == "__main__":
    main()
