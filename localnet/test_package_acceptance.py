"""Evidence packaging must not disclose token values or follow untrusted artifact links."""

import io
import tarfile
from pathlib import Path

import pytest

from localnet.package_acceptance import Archive


def test_token_value_prevents_publication(tmp_path: Path) -> None:
    artifact = tmp_path / "log"
    artifact.write_bytes(b"oops token-value-that-must-remain-private")
    with tarfile.open(fileobj=io.BytesIO(), mode="w") as output:
        bundle = Archive(output, (b"token-value-that-must-remain-private",))
        with pytest.raises(RuntimeError, match="Operator secret"):
            bundle.add(artifact, "log")
        assert not bundle.files
        assert not output.getmembers()


def test_rejected_link_is_metadata_and_target_is_never_archived(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.write_text("operator-token-value")
    data = tmp_path / "data"
    data.mkdir()
    (data / "README.md").symlink_to(private)
    (data / "main.py").write_text("print('hello')\n")
    with tarfile.open(fileobj=io.BytesIO(), mode="w") as output:
        bundle = Archive(output, (b"operator-token-value",))
        bundle.tree(data, "data")
        assert bundle.files["data/README.md"] == {"type": "symlink", "target": str(private)}
        assert [entry.name for entry in output.getmembers()] == ["data/main.py"]
