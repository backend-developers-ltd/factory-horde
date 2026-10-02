"""Select the same isolated application for source-checkout and installed-candidate checks."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

from installer.release import Installation, compose

SOURCE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Runtime:
    """Explicit local configuration and maintained Compose command, without copying secrets."""

    directory: Path
    config: dict[str, str]
    command: tuple[str, ...]
    service: str
    project: str

    @classmethod
    def load(cls, env_file: Path = SOURCE / ".env") -> Runtime:
        """Require local subnet/path isolation before any acceptance action.

        Raises:
            RuntimeError: The environment is not an isolated supported local installation.
        """
        env_file = env_file.absolute()
        if env_file.resolve() != env_file:
            raise RuntimeError("Acceptance configuration must not traverse symlinks")
        directory = env_file.parent
        config = {key: value or "" for key, value in dotenv_values(env_file).items()}
        if (
            config.get("ENVIRONMENT") != "localnet"
            or config.get("NETUID") != "2"
            or config.get("BITTENSOR_NETWORK") != "ws://subtensor:9944"
        ):
            raise RuntimeError("Acceptance requires isolated local subnet 2")
        if (directory / "installation.json").exists():
            installation = Installation.read(directory)
            if not installation.localnet or env_file.name != ".env":
                raise RuntimeError("Use the local installation's literal .env")
            expected_data = directory / "data"
            command = tuple(compose(directory, installation))
            service, project = installation.service, installation.project
        elif directory == SOURCE:
            expected_data = directory / "state/data"
            command = (str(SOURCE / "compose.sh"),)
            service, project = "factory-horde-localnet-executor", "factory-horde-localnet"
        else:
            raise RuntimeError("Use an installed candidate or the source-checkout localnet")
        if (
            config.get("FACTORY_HORDE_DATA_ROOT") != str(expected_data)
            or config.get("HOST_WALLET_DIR") != str(directory / "wallets")
            or expected_data.resolve() != expected_data
            or (directory / "wallets").resolve() != directory / "wallets"
        ):
            raise RuntimeError("Local data and wallets must stay inside their selected installation")
        return cls(directory, config, command, service, project)

    @property
    def state(self) -> Path:
        """Retained public check artifacts, separate from wallet/token files."""
        return self.directory / "state"

    @property
    def data(self) -> Path:
        """The selected executor/validator shared host data tree."""
        return Path(self.config["FACTORY_HORDE_DATA_ROOT"])

    @property
    def pylon_address(self) -> str:
        """Only a loopback connection to this local installation's Pylon."""
        return f"http://127.0.0.1:{int(self.config.get('PYLON_HOST_PORT') or '8000')}"

    def image(self, kind: Literal["factory", "judge", "validator", "submitter"]) -> str:
        """Use candidate registry pins or the explicit source-build development artifacts.

        Raises:
            RuntimeError: The selected image has not been configured.
        """
        selected = self.config.get(f"{kind.upper()}_IMAGE")
        if selected:
            return selected
        if kind in ("validator", "submitter"):
            return (self.state / f"{kind}-image.id").read_text().strip()
        images = dotenv_values(self.state / "published-images.env")
        selected = images.get(f"GHCR_{kind.upper()}_IMAGE")
        if not selected:
            raise RuntimeError(f"Missing {kind} image selection")
        return selected
