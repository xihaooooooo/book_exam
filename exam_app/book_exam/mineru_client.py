from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

import httpx

from .pdf_chunker import PdfChunk

_RESULT_HOST_SUFFIXES = (".openxlab.org.cn", ".mineru.net")
_DATA_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,128}")


class MinerUError(RuntimeError):
    """Raised when MinerU rejects a request or returns an invalid response."""


@dataclass(frozen=True, slots=True)
class MinerUJob:
    batch_id: str
    data_id: str


@dataclass(frozen=True, slots=True)
class MinerUStatus:
    state: str
    extracted_pages: int | None = None
    total_pages: int | None = None
    result_url: str | None = None
    error_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in {"done", "failed"}


@dataclass(frozen=True, slots=True)
class MinerUJobStatus:
    job: MinerUJob
    status: MinerUStatus


@dataclass(frozen=True, slots=True)
class MinerUBatchStatus:
    batch_id: str
    job_statuses: tuple[MinerUJobStatus, ...]

    @property
    def state(self) -> str:
        if any(item.status.state == "failed" for item in self.job_statuses):
            return "failed"
        if self.job_statuses and all(
            item.status.state == "done" for item in self.job_statuses
        ):
            return "done"
        return "processing"

    @property
    def is_terminal(self) -> bool:
        return self.state in {"done", "failed"}


class MinerUClient:
    """Submits local textbooks to MinerU's signed-upload API."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://mineru.net",
        http_client: httpx.Client | None = None,
    ) -> None:
        if not token.strip():
            raise ValueError("MinerU token is required")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=60)
        self._owns_http_client = http_client is None

    def submit_file(self, source_path: Path, *, data_id: str | None = None) -> MinerUJob:
        source_path = self._validate_pdf(source_path)
        data_id = data_id or uuid4().hex
        return self._submit_files(((source_path, data_id),))[0]

    def submit_chunks(self, chunks: Sequence[PdfChunk]) -> tuple[MinerUJob, ...]:
        if not chunks:
            raise ValueError("At least one PDF chunk is required")

        files = tuple(
            (self._validate_pdf(chunk.path), self._validate_data_id(chunk.data_id))
            for chunk in chunks
        )
        data_ids = [data_id for _, data_id in files]
        if len(data_ids) != len(set(data_ids)):
            raise ValueError("PDF chunk data IDs must be unique")
        return self._submit_files(files)

    def _submit_files(self, files: tuple[tuple[Path, str], ...]) -> tuple[MinerUJob, ...]:
        response = self._http.post(
            f"{self._base_url}/api/v4/file-urls/batch",
            headers={"Authorization": f"Bearer {self._token}"},
            json={
                "files": [
                    {"name": source_path.name, "data_id": data_id}
                    for source_path, data_id in files
                ],
                "model_version": "vlm",
                "language": "ch",
                "enable_formula": True,
                "enable_table": True,
            },
        )
        payload = self._success_payload(response, "request upload URL")
        batch_id = payload.get("batch_id")
        file_urls_value = payload.get("file_urls")
        if not isinstance(batch_id, str) or not isinstance(file_urls_value, list):
            raise MinerUError("MinerU returned invalid upload information")

        file_urls: list[str] = []
        for file_url in cast(list[object], file_urls_value):
            if not isinstance(file_url, str):
                raise MinerUError("MinerU returned invalid upload information")
            file_urls.append(file_url)
        if len(file_urls) != len(files):
            raise MinerUError("MinerU returned invalid upload information")

        for (source_path, _), file_url in zip(files, file_urls, strict=True):
            with source_path.open("rb") as source:
                upload_response = self._http.put(file_url, content=source)
            if upload_response.status_code < 200 or upload_response.status_code >= 300:
                raise MinerUError(
                    f"MinerU file upload failed with HTTP {upload_response.status_code}"
                )
        return tuple(MinerUJob(batch_id=batch_id, data_id=data_id) for _, data_id in files)

    def get_status(self, job: MinerUJob) -> MinerUStatus:
        return self.get_batch_status((job,)).job_statuses[0].status

    def get_batch_status(self, jobs: Sequence[MinerUJob]) -> MinerUBatchStatus:
        if not jobs:
            raise ValueError("At least one MinerU job is required")

        jobs = tuple(jobs)
        batch_ids = {job.batch_id for job in jobs}
        if len(batch_ids) != 1:
            raise ValueError("MinerU jobs must belong to the same batch")
        data_ids = [job.data_id for job in jobs]
        if len(data_ids) != len(set(data_ids)):
            raise ValueError("MinerU job data IDs must be unique")

        batch_id = jobs[0].batch_id
        response = self._http.get(
            f"{self._base_url}/api/v4/extract-results/batch/{batch_id}",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        payload = self._success_payload(response, "query parsing status")
        results_value = payload.get("extract_result")
        if not isinstance(results_value, list):
            raise MinerUError("MinerU returned invalid parsing status")

        results_by_data_id: dict[str, dict[str, object]] = {}
        for result_value in cast(list[object], results_value):
            if not isinstance(result_value, dict):
                continue
            result = cast(dict[str, object], result_value)
            data_id = result.get("data_id")
            if isinstance(data_id, str):
                results_by_data_id[data_id] = result

        job_statuses: list[MinerUJobStatus] = []
        for job in jobs:
            result = results_by_data_id.get(job.data_id)
            if result is None:
                raise MinerUError("MinerU response does not contain the submitted document")
            job_statuses.append(MinerUJobStatus(job, self._parse_status(result)))
        return MinerUBatchStatus(batch_id, tuple(job_statuses))

    def _parse_status(self, result: dict[str, object]) -> MinerUStatus:
        state_value = result.get("state")
        valid_states = {"waiting-file", "pending", "running", "converting", "done", "failed"}
        if not isinstance(state_value, str) or state_value not in valid_states:
            raise MinerUError("MinerU returned an unknown parsing state")

        progress_value = result.get("extract_progress")
        progress = (
            cast(dict[str, object], progress_value)
            if isinstance(progress_value, dict)
            else {}
        )
        result_url_value = result.get("full_zip_url")
        error_message_value = result.get("err_msg")
        return MinerUStatus(
            state=state_value,
            extracted_pages=self._optional_int(progress.get("extracted_pages")),
            total_pages=self._optional_int(progress.get("total_pages")),
            result_url=result_url_value if isinstance(result_url_value, str) else None,
            error_message=(
                error_message_value if isinstance(error_message_value, str) else None
            ),
        )

    def download_result(self, status: MinerUStatus, book_directory: Path) -> Path:
        book_directory = book_directory.resolve()
        return self._download_result(status, book_directory / "mineru-result.zip")

    def download_batch_results(
        self,
        batch_status: MinerUBatchStatus,
        book_directory: Path,
    ) -> tuple[Path, ...]:
        if batch_status.state != "done":
            raise MinerUError("MinerU batch result is not ready")

        result_directory = book_directory.resolve() / "mineru-results"
        destinations: list[Path] = []
        for job_status in batch_status.job_statuses:
            data_id = self._validate_data_id(job_status.job.data_id)
            destinations.append(
                self._download_result(
                    job_status.status,
                    result_directory / f"{data_id}.zip",
                )
            )
        return tuple(destinations)

    def _download_result(self, status: MinerUStatus, destination: Path) -> Path:
        if status.state != "done" or not status.result_url:
            raise MinerUError("MinerU parsing result is not ready")
        result_url = httpx.URL(status.result_url)
        host = result_url.host or ""
        if result_url.scheme != "https" or not any(
            host == suffix[1:] or host.endswith(suffix) for suffix in _RESULT_HOST_SUFFIXES
        ):
            raise MinerUError("MinerU returned an untrusted result URL")

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{uuid4().hex}.downloading"
        try:
            with self._http.stream("GET", result_url) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise MinerUError(f"MinerU result download failed with HTTP {response.status_code}")
                with temporary.open("xb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    @staticmethod
    def _success_payload(response: httpx.Response, operation: str) -> dict[str, object]:
        if response.status_code < 200 or response.status_code >= 300:
            raise MinerUError(f"MinerU could not {operation}: HTTP {response.status_code}")
        try:
            body_value: object = response.json()
        except ValueError as error:
            raise MinerUError("MinerU returned malformed JSON") from error
        if not isinstance(body_value, dict):
            raise MinerUError(f"MinerU could not {operation}")
        body = cast(dict[str, object], body_value)
        data_value = body.get("data")
        if body.get("code") != 0 or not isinstance(data_value, dict):
            raise MinerUError(f"MinerU could not {operation}")
        return cast(dict[str, object], data_value)

    @staticmethod
    def _optional_int(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _validate_pdf(source_path: Path) -> Path:
        source_path = source_path.resolve()
        if not source_path.is_file() or source_path.suffix.lower() != ".pdf":
            raise ValueError("A stored PDF file is required")
        return source_path

    @staticmethod
    def _validate_data_id(data_id: str) -> str:
        if not _DATA_ID_PATTERN.fullmatch(data_id) or data_id in {".", ".."}:
            raise ValueError("PDF chunk data ID is invalid")
        return data_id
