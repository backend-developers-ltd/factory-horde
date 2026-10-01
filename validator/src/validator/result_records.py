"""Immutable business outcomes and score-free Nexus projection references."""

from typing import Self
from uuid import UUID

from nexus.v1 import BlockBeat, Neuron
from pydantic import model_validator

from validator.records import (
    AcceptedResult,
    BlockHash,
    JobRequest,
    JobStatus,
    NonnegativeInt,
    Record,
    RelativePath,
    UtcTime,
)


class CompletionBlock(Record):
    """First acceptance observation's block, retained independently of later framework timestamps."""

    number: NonnegativeInt
    hash: BlockHash
    timestamp: NonnegativeInt

    @classmethod
    def from_beat(cls, beat: BlockBeat) -> Self:
        """Preserve the selected Nexus/Pylon block observation without changing timestamp units."""
        return cls(number=beat.block_number, hash=beat.block_hash, timestamp=beat.block_timestamp)


class JobResult(Record):
    """One immutable decision; the accepted score exists only in this shared-tree record."""

    request: JobRequest
    status: JobStatus
    result_id: UUID
    processing_started: UtcTime
    processing_finished: UtcTime
    completion: CompletionBlock
    accepted: AcceptedResult | None = None
    failure: str | None = None

    @model_validator(mode="after")
    def _consistent_decision(self) -> Self:
        if not self.request.same_job(self.status) or not self.status.confirmed_stopped:
            raise ValueError("Result requires attributed confirmed termination")
        if self.result_id != self.request.projection_id or self.processing_started > self.processing_finished:
            raise ValueError("Invalid stable result identity or timing")
        if self.failure is not None and (not self.failure or self.accepted is not None):
            raise ValueError("Failed results cannot carry a score")
        if self.failure is None and not self.status.application_succeeded:
            raise ValueError("Success requires clean Docker termination")
        if self.request.kind == "factory" and self.accepted is not None:
            raise ValueError("Factories do not carry scores")
        if self.request.kind == "judge" and (self.failure is None) != (self.accepted is not None):
            raise ValueError("Successful judges require accepted scores")
        accepted = self.accepted
        if accepted is not None and (
            not self.request.same_job(accepted)
            or accepted.result_id != self.result_id
            or accepted.processing_started != self.processing_started
            or accepted.processing_finished != self.processing_finished
            or accepted.completion_block != self.completion.number
            or accepted.completion_block_hash != self.completion.hash
        ):
            raise ValueError("Accepted score metadata differs from its execution")
        return self


class ResultProjection(Record):
    """Nexus routing metadata references the business result without duplicating its score."""

    result_id: UUID
    result_path: RelativePath
    target: Neuron
