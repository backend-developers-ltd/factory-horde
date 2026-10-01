"""
Bootstrap only FactoryHorde's isolated local chain using the miner project's lock.

Sets up the local subnet infrastructure:
- Transfers TAO from Alice (pre-funded devnet account) to owner and validator wallets
- Creates and activates subnet (netuid 2, since netuid 1 is owned by zero-key and is unusable)
- Registers and stakes validator and registers five distinct miner identities

register_subnet has no netuid parameter — the chain auto-assigns the next free slot. We
assume it matches NETUID from localnet/.env and abort if not, so pylon/validator/monitor
don't end up pointed at a different subnet than the one we configured.

Prerequisites: localnet/prepare.sh; localnet/compose.sh up -d --wait subtensor.

Usage: uv run --project miner python localnet/bootstrap.py
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import bittensor as bt
from bittensor.utils.balance import Balance
from bittensor_wallet import Keypair, Wallet
from dotenv import dotenv_values

LOCALNET_ROOT = Path(__file__).resolve().parent
WALLETS_DIR = LOCALNET_ROOT / "wallets"
VALIDATOR_STAKE_TAO = 1000.0
FUND_AMOUNT_TAO = 10_000.0
EXPECTED_NETUID = 2

# Disabled until we have support for fast blocks in pylon
SUBNET_COMMIT_REVEAL_ENABLED = False

# AdminFreezeWindow gates subnet-owner admin extrinsics during the last N blocks of each
# tempo. Disabled on localnet so bootstrap/operator hyperparameter calls are never rejected
# with AdminActionProhibitedDuringWeightsWindow, otherwise the random rejections get annoying fast.
ADMIN_FREEZE_WINDOW = 0


def wait_for_subtensor(network: str, retries: int = 30, delay: float = 2.0) -> bt.Subtensor:
    """Wait for the local subtensor to become reachable."""
    for attempt in range(retries):
        try:
            sub = bt.Subtensor(network=network)
            block = sub.get_current_block()
            print(f"Connected to subtensor at block {block}")
            return sub
        except Exception:
            print(f"Waiting for subtensor... (attempt {attempt + 1}/{retries})")
            time.sleep(delay)
    print("Failed to connect to subtensor. Is docker compose running?")
    sys.exit(1)


def get_alice_wallet() -> Wallet:
    """Create a wallet backed by Alice's well-known devnet keypair."""
    alice_kp = Keypair.create_from_uri("//Alice")
    wallet = Wallet(name="alice", path=str(WALLETS_DIR))
    wallet.set_coldkey(keypair=alice_kp, encrypt=False, overwrite=True)
    wallet.set_coldkeypub(keypair=alice_kp, overwrite=True)
    wallet.set_hotkey(keypair=alice_kp, encrypt=False, overwrite=True)
    return wallet


def get_or_create_wallet(name: str) -> Wallet:
    """Create a wallet if it doesn't exist, using localnet wallets directory."""
    wallet = Wallet(name=name, path=str(WALLETS_DIR))
    # The SDK prints secret recovery words when creating keys; local disposable keys
    # are written directly so bootstrap logs never contain them.
    if not wallet.coldkey_file.exists_on_device():
        key = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        wallet.set_coldkey(keypair=key, encrypt=False)
        wallet.set_coldkeypub(keypair=key)
    if not wallet.hotkey_file.exists_on_device():
        wallet.set_hotkey(keypair=Keypair.create_from_mnemonic(Keypair.generate_mnemonic()), encrypt=False)
    return wallet


def fund_wallet(subtensor: bt.Subtensor, alice: Wallet, target: Wallet) -> None:
    """Transfer TAO from Alice to a target wallet if balance is low."""
    balance = subtensor.get_balance(target.coldkey.ss58_address)
    if balance > Balance.from_tao(100.0):
        print(f"  {target.name} already funded (balance: {balance})")
        return
    print(f"  Funding {target.name} with {FUND_AMOUNT_TAO} TAO from Alice...")
    response = subtensor.transfer(
        wallet=alice,
        destination_ss58=target.coldkey.ss58_address,
        amount=Balance.from_tao(FUND_AMOUNT_TAO),
        wait_for_inclusion=True,
        wait_for_finalization=True,
        mev_protection=False,
    )
    if not response.success:
        print(f"  Transfer failed: {response.message}")
        sys.exit(1)
    print(f"  {target.name} funded")


def get_subnet_owner_coldkey(subtensor: bt.Subtensor, netuid: int) -> str | None:
    """Return owner coldkey of an existing subnet, or None if it doesn't exist.

    Raises:
        RuntimeError: The subnet exists but its owner cannot be read.
    """
    if not subtensor.subnet_exists(netuid=netuid):
        return None
    subnet = subtensor.subnet(netuid=netuid)
    if subnet is None:
        raise RuntimeError("Existing subnet has no readable owner")
    return subnet.owner_coldkey


def create_subnet(subtensor: bt.Subtensor, owner: Wallet) -> int:
    """Create a subnet at EXPECTED_NETUID if missing. Returns the netuid. Idempotent."""
    existing_owner = get_subnet_owner_coldkey(subtensor, EXPECTED_NETUID)
    if existing_owner is not None:
        if existing_owner == owner.coldkey.ss58_address:
            print(f"Subnet {EXPECTED_NETUID} already exists and is owned by us")
            return EXPECTED_NETUID
        print(
            f"Subnet {EXPECTED_NETUID} already exists but is owned by {existing_owner}, "
            f"not our owner ({owner.coldkey.ss58_address}). "
            "Refusing to modify a subnet belonging to another wallet."
        )
        sys.exit(1)

    print("Creating subnet...")
    response = subtensor.register_subnet(
        wallet=owner,
        wait_for_inclusion=True,
        wait_for_finalization=True,
        mev_protection=False,
    )
    if not response.success:
        print(f"Subnet registration failed: {response.message}")
        sys.exit(1)

    # The chain auto-assigns the next free netuid; there's no extrinsic to request one.
    # Bail on mismatch so pylon/validator/monitor aren't silently misconfigured.
    new_owner = get_subnet_owner_coldkey(subtensor, EXPECTED_NETUID)
    if new_owner != owner.coldkey.ss58_address:
        print(
            f"Subnet at netuid {EXPECTED_NETUID} is owned by {new_owner}, expected {owner.coldkey.ss58_address}. "
            f"Chain may have assigned a different netuid. "
            "Refusing to continue with a different subnet."
        )
        sys.exit(1)
    print(f"Subnet created with netuid {EXPECTED_NETUID}")
    return EXPECTED_NETUID


def activate_subnet(subtensor: bt.Subtensor, owner: Wallet, netuid: int) -> None:
    """Activate a subnet via start_call. Idempotent: noop if already active."""
    if subtensor.is_subnet_active(netuid=netuid):
        print(f"Subnet {netuid} already active")
        return

    current_block = subtensor.get_current_block()
    delay_blocks = subtensor.get_start_call_delay()
    target_block = current_block + delay_blocks + 1
    print(f"Waiting {delay_blocks} blocks to activate subnet (current: {current_block}, target: {target_block})...")
    subtensor.wait_for_block(target_block)

    print("Activating subnet...")
    response = subtensor.start_call(
        wallet=owner,
        netuid=netuid,
        wait_for_inclusion=True,
        wait_for_finalization=True,
        mev_protection=False,
    )
    if not response.success:
        print(f"Subnet activation failed: {response.message}")
        sys.exit(1)
    print(f"Subnet {netuid} activated")


def set_admin_freeze_window(subtensor: bt.Subtensor, sudo: Wallet, window: int) -> None:
    """Set chain-wide AdminFreezeWindow via Sudo. Requires the root key (Alice on localnet). Idempotent."""
    current = subtensor.get_admin_freeze_window()
    if current == window:
        print(f"  admin freeze window already {window}")
        return
    print(f"  Setting admin freeze window {current} -> {window} via sudo...")
    inner = subtensor.compose_call(
        call_module="AdminUtils",
        call_function="sudo_set_admin_freeze_window",
        call_params={"window": window},
    )
    response = subtensor.sign_and_send_extrinsic(
        call=subtensor.compose_call(call_module="Sudo", call_function="sudo", call_params={"call": inner}),
        wallet=sudo,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )
    if not response.success:
        print(f"  set_admin_freeze_window failed: {response.message}")
        sys.exit(1)
    new_val = subtensor.get_admin_freeze_window()
    if new_val != window:
        print(f"  set_admin_freeze_window failed: on-chain value is {new_val}, expected {window}")
        sys.exit(1)
    print(f"  admin freeze window set to {window}")


def set_subnet_tempo(subtensor: bt.Subtensor, sudo: Wallet, netuid: int, tempo: int) -> None:
    """Set subnet tempo via Sudo. Requires the root key — not callable by subnet owners. Idempotent."""
    current = subtensor.tempo(netuid)
    if current == tempo:
        print(f"  tempo already {tempo}")
        return
    print(f"  Setting tempo {current} -> {tempo} via sudo...")
    inner = subtensor.compose_call(
        call_module="AdminUtils",
        call_function="sudo_set_tempo",
        call_params={"netuid": netuid, "tempo": tempo},
    )
    response = subtensor.sign_and_send_extrinsic(
        call=subtensor.compose_call(call_module="Sudo", call_function="sudo", call_params={"call": inner}),
        wallet=sudo,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )
    if not response.success:
        print(f"  set_tempo failed: {response.message}")
        sys.exit(1)
    new_val = subtensor.tempo(netuid)
    if new_val != tempo:
        print(f"  set_tempo failed: on-chain value is {new_val}, expected {tempo}")
        sys.exit(1)
    print(f"  tempo set to {tempo}")


def set_commit_reveal_enabled(subtensor: bt.Subtensor, owner: Wallet, netuid: int, enabled: bool) -> None:
    """Toggle commit-reveal weight submission on a subnet. Idempotent.

    The AdminUtils extrinsic accepts subnet owner or root.
    """
    current = bool(subtensor.get_hyperparameter("CommitRevealWeightsEnabled", netuid=netuid))
    if current == enabled:
        print(f"  commit-reveal already {'enabled' if enabled else 'disabled'}")
        return
    print(f"  Setting commit-reveal {current} -> {enabled} as subnet owner...")
    call = subtensor.compose_call(
        call_module="AdminUtils",
        call_function="sudo_set_commit_reveal_weights_enabled",
        call_params={"netuid": netuid, "enabled": enabled},
    )
    response = subtensor.sign_and_send_extrinsic(
        call=call,
        wallet=owner,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )
    if not response.success:
        print(f"  set_commit_reveal failed: {response.message}")
        sys.exit(1)
    # ExtrinsicResponse.success reports inclusion, not inner dispatch — verify state.
    new_val = bool(subtensor.get_hyperparameter("CommitRevealWeightsEnabled", netuid=netuid))
    if new_val != enabled:
        print(f"  set_commit_reveal failed: on-chain value is {new_val}, expected {enabled}")
        sys.exit(1)
    print(f"  commit-reveal {'enabled' if enabled else 'disabled'}")


def register_neuron(subtensor: bt.Subtensor, wallet: Wallet, netuid: int) -> None:
    """Register a neuron if not already registered."""
    if subtensor.is_hotkey_registered(wallet.hotkey.ss58_address, netuid):
        print(f"  {wallet.name} already registered on subnet {netuid}")
        return
    print(f"  Registering {wallet.name} on subnet {netuid}...")
    response = subtensor.burned_register(
        wallet=wallet,
        netuid=netuid,
        wait_for_inclusion=True,
        wait_for_finalization=True,
        mev_protection=False,
    )
    if not response.success:
        print(f"  Registration failed: {response.message}")
        sys.exit(1)
    print(f"  {wallet.name} registered")


def stake_validator(subtensor: bt.Subtensor, wallet: Wallet, netuid: int) -> None:
    """Ensure the validator has the target localnet stake."""
    current_stake = subtensor.get_stake(
        coldkey_ss58=wallet.coldkey.ss58_address,
        hotkey_ss58=wallet.hotkey.ss58_address,
        netuid=netuid,
    )
    if current_stake > Balance.from_tao(0, netuid=netuid):
        print(f"  {wallet.name} already staked (stake: {current_stake})")
        return

    # Local alpha's price changes. Once funded, repeated bootstrap must not keep
    # spending TAO to chase a nominal alpha target.
    amount_to_add = Balance.from_tao(VALIDATOR_STAKE_TAO)
    print(f"  Staking {amount_to_add} for {wallet.name}...")
    response = subtensor.add_stake(
        wallet=wallet,
        netuid=netuid,
        hotkey_ss58=wallet.hotkey.ss58_address,
        amount=amount_to_add,
        wait_for_inclusion=True,
        wait_for_finalization=True,
        mev_protection=False,
    )
    if not response.success:
        print(f"  Staking failed: {response.message}")
        sys.exit(1)
    print(f"  {wallet.name} staked")


def ensure_mechanism(subtensor: bt.Subtensor, alice: Wallet, netuid: int, mechanism: int) -> None:
    """Enable the requested mechanism on this local subnet and read back its count.

    Raises:
        RuntimeError: Mechanism creation or independent read-back fails.
    """
    if subtensor.get_mechanism_count(netuid) > mechanism:
        return
    # The selected runtime bounds UID capacity across all mechanisms; match
    # Pylon's local-chain integration fixture before enabling a second mechanism.
    capacity = subtensor.compose_call(
        call_module="AdminUtils",
        call_function="sudo_set_max_allowed_uids",
        call_params={"netuid": netuid, "max_allowed_uids": 64},
    )
    capacity_response = subtensor.sign_and_send_extrinsic(
        call=subtensor.compose_call(call_module="Sudo", call_function="sudo", call_params={"call": capacity}),
        wallet=alice,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )
    if not capacity_response.success or subtensor.get_hyperparameter("MaxAllowedUids", netuid) != 64:
        raise RuntimeError("Local mechanism UID capacity configuration failed")
    inner = subtensor.compose_call(
        call_module="AdminUtils",
        call_function="sudo_set_mechanism_count",
        call_params={"netuid": netuid, "mechanism_count": mechanism + 1},
    )
    response = subtensor.sign_and_send_extrinsic(
        call=subtensor.compose_call(call_module="Sudo", call_function="sudo", call_params={"call": inner}),
        wallet=alice,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )
    if not response.success or subtensor.get_mechanism_count(netuid) <= mechanism:
        raise RuntimeError(f"Local mechanism configuration failed: {response.message}")


@dataclass(frozen=True)
class Registration:
    """Public identity evidence, never wallet secrets or API tokens."""

    identity: str
    uid: int
    hotkey: str
    coldkey: str


def verify_registrations(network: str, wallets: list[Wallet], mechanism: int) -> None:
    """Independently read a block-aligned registration snapshot directly from Subtensor.

    Raises:
        RuntimeError: An identity is missing, mismatched or shares a UID.
    """
    with bt.Subtensor(network=network) as chain:
        block = chain.get_current_block()
        registrations: list[Registration] = []
        neurons = {neuron.hotkey: neuron for neuron in chain.neurons_lite(EXPECTED_NETUID, block=block)}
        for wallet in wallets:
            hotkey = wallet.hotkey.ss58_address
            neuron = neurons.get(hotkey)
            if neuron is None or neuron.coldkey != wallet.coldkeypub.ss58_address:
                raise RuntimeError(f"Independent registration check failed for {wallet.name}")
            registrations.append(Registration(wallet.name, neuron.uid, hotkey, neuron.coldkey))
        if len({entry.uid for entry in registrations}) != len(wallets):
            raise RuntimeError("Identity UIDs are not distinct")
        evidence = {
            "netuid": EXPECTED_NETUID,
            "mechanism_id": mechanism,
            "mechanism_count": chain.get_mechanism_count(EXPECTED_NETUID, block=block),
            "block": block,
            "block_hash": chain.get_block_hash(block),
            "registrations": [asdict(entry) for entry in registrations],
        }
    destination = LOCALNET_ROOT / "state" / "registrations.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(evidence, indent=2) + "\n")
    temporary.replace(destination)
    print(f"Verified {len(registrations)} registrations directly at block {block}; evidence: {destination}")


def main() -> None:
    """Prepare local identities sequentially to avoid transaction nonce collisions.

    Raises:
        ValueError: Configuration does not describe the isolated localnet.
    """
    config = dotenv_values(LOCALNET_ROOT / ".env")
    if (
        config.get("ENVIRONMENT") != "localnet"
        or config.get("NETUID") != "2"
        or config.get("BITTENSOR_NETWORK") != "ws://subtensor:9944"
        or config.get("HOST_WALLET_DIR") != str(WALLETS_DIR)
    ):
        raise ValueError("Run localnet/prepare.sh; bootstrap accepts only the isolated localnet configuration")
    port = int(config.get("SUBTENSOR_HOST_PORT") or "9944")
    if not 1024 <= port <= 65535:
        raise ValueError("Invalid local Subtensor port")
    network = f"ws://127.0.0.1:{port}"
    mechanism = int(config.get("MECHANISM_ID") or "0")
    if mechanism not in (0, 1):
        raise ValueError("Local bootstrap supports mechanism 0 or 1")
    subtensor = wait_for_subtensor(network)
    alice = get_alice_wallet()

    alice_balance = subtensor.get_balance(alice.coldkey.ss58_address)
    print(f"Alice balance: {alice_balance}")

    # Owner: creates and owns the subnet
    print("\n--- Setting up owner wallet ---")
    owner = get_or_create_wallet("owner")
    fund_wallet(subtensor, alice, owner)

    print("\n--- Creating subnet ---")
    netuid = create_subnet(subtensor, owner)

    print("\n--- Disabling admin freeze window ---")
    set_admin_freeze_window(subtensor, alice, ADMIN_FREEZE_WINDOW)

    print("\n--- Configuring subnet hyperparameters ---")
    set_subnet_tempo(subtensor, alice, netuid, int(config.get("SUBNET_TEMPO") or "360"))
    set_commit_reveal_enabled(subtensor, owner, netuid, SUBNET_COMMIT_REVEAL_ENABLED)
    ensure_mechanism(subtensor, alice, netuid, mechanism)

    print("\n--- Activating subnet ---")
    activate_subnet(subtensor, owner, netuid)

    # Validator: registers and stakes
    print("\n--- Setting up validator ---")
    validator = get_or_create_wallet("validator")
    fund_wallet(subtensor, alice, validator)
    register_neuron(subtensor, validator, netuid)
    stake_validator(subtensor, validator, netuid)
    wallets = [owner, validator]
    for index in range(1, 6):
        miner = get_or_create_wallet(f"miner{index}")
        fund_wallet(subtensor, alice, miner)
        register_neuron(subtensor, miner, netuid)
        wallets.append(miner)
    subtensor.close()
    verify_registrations(network, wallets, mechanism)


if __name__ == "__main__":
    main()
