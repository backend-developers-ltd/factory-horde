"""FactoryHorde validator startup: chain observation, with dispatch disabled until task wiring."""

from __future__ import annotations

import logging
import os
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
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.litestar import LitestarIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration

from validator.chain_observer import ChainObservation, ChainObserverNode
from validator.logging_config import LoggingSettings, configure_logging
from validator.otel import OtelSettings, setup_otel
from validator.response_logger import ErrorLoggerNode, MessageLoggerNode


class Settings(PylonClientSettingsMixin, BaseSettings):
    """Application configuration; chain writes and factory dispatch remain disabled."""

    model_config = SettingsConfigDict(env_prefix="VALIDATOR_", extra="ignore")

    netuid: int = Field(validation_alias=AliasChoices("VALIDATOR_NETUID", "NETUID"))
    mechanism_id: MechanismId = Field(default=MechanismId(0), ge=0, validation_alias="MECHANISM_ID")
    data_root: Path = Path("/var/lib/factory-horde")
    dispatch_enabled: bool = False

    @model_validator(mode="after")
    def _dispatch_not_implemented(self) -> Self:
        if self.dispatch_enabled:
            raise ValueError("FactoryHorde dispatch is not implemented yet")
        return self


class Validator(NexusValidator):
    """Containerized Nexus runtime observing Pylon's chain clock without creating jobs."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        observer = ChainObserverNode(settings.data_root, settings.mechanism_id)
        errors = ErrorLoggerNode("factory-horde-errors")
        observed = MessageLoggerNode[ChainObservation]("factory-horde-observed")
        self.connect(self.subnet_clock.source, observer.sink)
        self.connect(observer.error, errors.sink)
        self.connect(observer.ok, observed.sink)


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
