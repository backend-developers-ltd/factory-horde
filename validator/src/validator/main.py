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
    MechanismId,
    NexusValidator,
    PylonClientSettingsMixin,
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
from validator.logging_config import LoggingSettings, configure_logging
from validator.otel import OtelSettings, setup_otel
from validator.records import ImageReference, MinerHotkey
from validator.response_logger import ErrorLoggerNode, MessageLoggerNode
from validator.result_repository import ResultRepository
from validator.round_actor import RoundCoordinatorNode
from validator.round_repository import RoundRepository
from validator.tasks import FileTasks


class Settings(PylonClientSettingsMixin, BaseSettings):
    """Explicit dispatch configuration; chain weight writes are added separately."""

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
        return self


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
        if settings.dispatch_enabled and settings.validator_hotkey is not None and settings.judge_image is not None:
            discovery = partial(
                freeze_via_pylon,
                self.results.files,
                Config(
                    address=settings.pylon_service_address,
                    identity_name=IdentityName(settings.pylon_identity_name or ""),
                    identity_token=PylonAuthToken(settings.pylon_identity_token or ""),
                    open_access_token=PylonAuthToken(settings.pylon_open_access_token),
                ),
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
            self.connect(coordinator.ok, taps=(MessageLoggerNode[RoundTick]("factory-horde-rounds").sink,))


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
