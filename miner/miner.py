"""Publish one public GHCR manifest reference through the miner's own Pylon identity."""

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

import click
from prometheus_client import Counter, Histogram
from pylon_client.artanis import (
    BasePylonException,
    BlockNumber,
    CommitmentDataBytes,
    Config,
    IdentityName,
    NetUid,
    PylonAuthToken,
    PylonBadGateway,
    PylonClient,
    PylonNotFound,
    PylonRequestException,
    PylonTimeout,
)
from pylon_client.artanis.v1 import GetCommitmentResponse, GetNeuronsResponse, SetCommitmentResponse
from tenacity import Retrying, stop_after_attempt

# The selected Pylon 2.3.3 writer uses RawN, whose supported maximum is Raw128.
COMMITMENT_MAX_BYTES = 128
IMAGE_PATTERN = r"ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}"
_events = Counter("factory_horde_submissions_total", "One-shot submission outcomes", ("outcome",))
_latency = Histogram("factory_horde_submission_seconds", "Submission and read-back duration")


class SubmissionError(RuntimeError):
    """Publication is invalid, misconfigured or still unconfirmed; never claim success."""


class SubmissionApi(Protocol):
    """The version-one identity API used by the one-shot submitter."""

    @property
    def netuid(self) -> NetUid: ...

    def get_latest_neurons(self) -> GetNeuronsResponse: ...
    def get_neurons(self, block_number: BlockNumber) -> GetNeuronsResponse: ...
    def get_own_commitment(self) -> GetCommitmentResponse: ...
    def set_commitment(self, commitment: CommitmentDataBytes) -> SetCommitmentResponse: ...


@dataclass(frozen=True)
class Publication:
    """Evidence from an exact read-back, including the original commitment block."""

    outcome: Literal["published", "unchanged", "recovered"]
    netuid: int
    hotkey: str
    image: str
    commitment_block: int
    observed_block: int
    observed_block_hash: str


def validate_reference(image: str) -> str:
    """Accept a complete GHCR digest that fits the selected writer's byte bound.

    Raises:
        ValueError: The registry, digest syntax or encoded length is unsupported.
    """
    if re.fullmatch(IMAGE_PATTERN, image) is None:
        raise ValueError("Expected ghcr.io/owner/image@sha256:<64 lowercase hexadecimal characters>")
    if len(image.encode("utf-8")) > COMMITMENT_MAX_BYTES:
        raise ValueError(f"The selected Pylon writer accepts at most {COMMITMENT_MAX_BYTES} UTF-8 bytes")
    return image


def decode_reference(value: str) -> str:
    """Decode only unambiguous v1 hexadecimal UTF-8 commitments.

    Raises:
        ValueError: Hexadecimal, UTF-8 or reference validation fails.
    """
    if re.fullmatch(r"(?:0x)?(?:[0-9a-fA-F]{2})+", value) is None:
        raise ValueError("Malformed hexadecimal commitment")
    return validate_reference(CommitmentDataBytes.fromhex(value).decode("utf-8"))


def _read(api: SubmissionApi) -> GetCommitmentResponse | None:
    try:
        return api.get_own_commitment()
    except PylonNotFound:
        return None


def _matches(response: GetCommitmentResponse | None, image: str) -> bool:
    if response is None:
        return False
    try:
        return decode_reference(response.commitment) == image
    except ValueError:
        # An invalid old commitment must not prevent a miner from correcting it.
        return False


def _evidence(
    api: SubmissionApi,
    response: GetCommitmentResponse,
    image: str,
    netuid: int,
    outcome: Literal["published", "unchanged", "recovered"],
) -> Publication:
    membership = api.get_neurons(response.block.number)
    if api.netuid != netuid or membership.block != response.block or response.hotkey not in membership.neurons:
        raise SubmissionError("Read-back is not attributable to a registered identity at the observed block")
    return Publication(
        outcome,
        netuid,
        response.hotkey,
        image,
        response.commitment_block_number,
        response.block.number,
        response.block.hash,
    )


def submit(api: SubmissionApi, image: str, netuid: int, *, confirm_seconds: float = 150) -> Publication:
    """Read first, write at most once, then require exact registered read-back.

    HTTP timeouts can leave the service's shielded write running. Recovery polls
    reads only; automatic HTTP write retries must be disabled in the client config.

    Raises:
        SubmissionError: Configuration changes or exact confirmation never arrives.
        ValueError: The reference or confirmation period is invalid.
    """
    validate_reference(image)
    if confirm_seconds <= 0:
        raise ValueError("Confirmation period must be positive")
    with _latency.time():
        try:
            api.get_latest_neurons()  # Resolves identity-scoped netuid before any mutation.
            if api.netuid != netuid:
                raise SubmissionError("Configured Pylon identity belongs to a different subnet")
            previous = _read(api)
            if previous is not None and _matches(previous, image):
                result = _evidence(api, previous, image, netuid, "unchanged")
            else:
                outcome: Literal["published", "recovered"] = "published"
                try:
                    api.set_commitment(CommitmentDataBytes(image.encode("utf-8")))
                except PylonRequestException, PylonBadGateway:
                    outcome = "recovered"
                deadline = time.monotonic() + confirm_seconds
                while True:
                    try:
                        response = _read(api)
                    except PylonRequestException:
                        response = None
                    if response is not None and _matches(response, image):
                        result = _evidence(api, response, image, netuid, outcome)
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SubmissionError("Publication remains unconfirmed; read before resubmitting")
                    time.sleep(min(2, remaining))
        except Exception:
            _events.labels("error").inc()
            raise
        _events.labels(result.outcome).inc()
        return result


@click.command(help="Publish IMAGE once and confirm its chain value. Supply PYLON_IDENTITY_TOKEN in the environment.")
@click.argument("image")
@click.option("--pylon-address", envvar="PYLON_ADDRESS", required=True)
@click.option("--identity", envvar="PYLON_IDENTITY", required=True)
@click.option("--netuid", type=click.IntRange(min=0), envvar="NETUID", required=True)
@click.option("--confirm-seconds", type=click.FloatRange(min=1, max=600), default=150, show_default=True)
def main(image: str, pylon_address: str, identity: str, netuid: int, confirm_seconds: float) -> None:
    """Publish IMAGE once and exit after exact chain-facing confirmation.

    Supply PYLON_IDENTITY_TOKEN through the environment, never as a command argument.

    Raises:
        click.ClickException: Configuration, publication or read-back fails.
    """
    started = time.monotonic()
    try:
        validate_reference(image)
        token = os.environ.get("PYLON_IDENTITY_TOKEN")
        if not token:
            raise click.ClickException("Set PYLON_IDENTITY_TOKEN in the environment")
        config = Config(
            address=pylon_address,
            identity_name=IdentityName(identity),
            identity_token=PylonAuthToken(token),
            retry=Retrying(stop=stop_after_attempt(1), reraise=True),
            timeout=PylonTimeout(read=confirm_seconds),
        )
        with PylonClient(config) as client:
            result = submit(client.v1.identity, image, netuid, confirm_seconds=confirm_seconds)
    except (BasePylonException, SubmissionError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps({**asdict(result), "elapsed_seconds": time.monotonic() - started}))


if __name__ == "__main__":
    main()
