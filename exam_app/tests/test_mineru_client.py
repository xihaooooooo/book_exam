import json
import tempfile
import unittest
from pathlib import Path

import httpx

from book_exam import PdfChunk
from book_exam.mineru_client import (
    MinerUBatchStatus,
    MinerUClient,
    MinerUJob,
    MinerUJobStatus,
    MinerUStatus,
)


class MinerUClientTests(unittest.TestCase):
    def test_download_batch_results_saves_one_zip_per_data_id(self) -> None:
        archives = {
            "https://cdn-mineru.openxlab.org.cn/first.zip": b"PK\x03\x04first",
            "https://cdn-mineru.openxlab.org.cn/second.zip": b"PK\x03\x04second",
            "https://cdn-mineru.openxlab.org.cn/third.zip": b"PK\x03\x04third",
        }

        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=archives[str(request.url)])

        job_statuses = tuple(
            MinerUJobStatus(
                MinerUJob("batch-456", data_id),
                MinerUStatus(state="done", result_url=result_url),
            )
            for data_id, result_url in (
                ("chunk-0001-0200", "https://cdn-mineru.openxlab.org.cn/first.zip"),
                ("chunk-0201-0400", "https://cdn-mineru.openxlab.org.cn/second.zip"),
                ("chunk-0401-0401", "https://cdn-mineru.openxlab.org.cn/third.zip"),
            )
        )
        batch = MinerUBatchStatus("batch-456", job_statuses)
        http_client = httpx.Client(transport=httpx.MockTransport(handle))
        client = MinerUClient("secret-token", http_client=http_client)

        with tempfile.TemporaryDirectory() as directory:
            destinations = client.download_batch_results(batch, Path(directory))

            self.assertEqual(
                [path.name for path in destinations],
                [
                    "chunk-0001-0200.zip",
                    "chunk-0201-0400.zip",
                    "chunk-0401-0401.zip",
                ],
            )
            self.assertEqual(
                [path.read_bytes() for path in destinations],
                [b"PK\x03\x04first", b"PK\x03\x04second", b"PK\x03\x04third"],
            )
            result_directory = Path(directory) / "mineru-results"
            self.assertEqual(list(result_directory.glob("*.downloading")), [])

    def test_get_batch_status_queries_once_and_preserves_job_order(self) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": "chunk-0201-0400",
                                "state": "running",
                                "extract_progress": {
                                    "extracted_pages": 80,
                                    "total_pages": 200,
                                },
                            },
                            {
                                "data_id": "chunk-0001-0200",
                                "state": "done",
                                "full_zip_url": "https://cdn-mineru.openxlab.org.cn/first.zip",
                            },
                            {
                                "data_id": "chunk-0401-0401",
                                "state": "pending",
                            },
                        ]
                    },
                },
            )

        jobs = (
            MinerUJob("batch-456", "chunk-0001-0200"),
            MinerUJob("batch-456", "chunk-0201-0400"),
            MinerUJob("batch-456", "chunk-0401-0401"),
        )
        http_client = httpx.Client(transport=httpx.MockTransport(handle))
        client = MinerUClient("secret-token", http_client=http_client)

        batch = client.get_batch_status(jobs)

        self.assertEqual(len(requests), 1)
        self.assertEqual(
            str(requests[0].url),
            "https://mineru.net/api/v4/extract-results/batch/batch-456",
        )
        self.assertEqual(batch.state, "processing")
        self.assertFalse(batch.is_terminal)
        self.assertEqual(
            [item.job.data_id for item in batch.job_statuses],
            ["chunk-0001-0200", "chunk-0201-0400", "chunk-0401-0401"],
        )
        self.assertEqual(
            [item.status.state for item in batch.job_statuses],
            ["done", "running", "pending"],
        )
        self.assertEqual(
            batch.job_statuses[0].status.result_url,
            "https://cdn-mineru.openxlab.org.cn/first.zip",
        )

    def test_submit_chunks_requests_one_batch_and_uploads_every_pdf(self) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": "batch-456",
                            "file_urls": [
                                "https://uploads.example/first",
                                "https://uploads.example/second",
                                "https://uploads.example/third",
                            ],
                        },
                    },
                )
            return httpx.Response(200)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunks = []
            for index, page_range in enumerate(((1, 200), (201, 400), (401, 401)), start=1):
                start_page, end_page = page_range
                data_id = f"chunk-{start_page:04d}-{end_page:04d}"
                path = root / f"{data_id}.pdf"
                path.write_bytes(f"%PDF-1.7\nchunk {index}".encode())
                chunks.append(PdfChunk(path, start_page, end_page, data_id))

            http_client = httpx.Client(transport=httpx.MockTransport(handle))
            client = MinerUClient("secret-token", http_client=http_client)

            jobs = client.submit_chunks(chunks)

        self.assertEqual(
            jobs,
            (
                MinerUJob("batch-456", "chunk-0001-0200"),
                MinerUJob("batch-456", "chunk-0201-0400"),
                MinerUJob("batch-456", "chunk-0401-0401"),
            ),
        )
        self.assertEqual([request.method for request in requests], ["POST", "PUT", "PUT", "PUT"])
        body = json.loads(requests[0].content)
        self.assertEqual(
            body["files"],
            [
                {"name": "chunk-0001-0200.pdf", "data_id": "chunk-0001-0200"},
                {"name": "chunk-0201-0400.pdf", "data_id": "chunk-0201-0400"},
                {"name": "chunk-0401-0401.pdf", "data_id": "chunk-0401-0401"},
            ],
        )
        self.assertEqual(
            [request.content for request in requests[1:]],
            [b"%PDF-1.7\nchunk 1", b"%PDF-1.7\nchunk 2", b"%PDF-1.7\nchunk 3"],
        )

    def test_submit_file_requests_upload_url_then_uploads_pdf(self) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": "batch-123",
                            "file_urls": ["https://uploads.example/signed"],
                        },
                    },
                )
            return httpx.Response(200)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            source.write_bytes(b"%PDF-1.7\ntextbook")
            http_client = httpx.Client(transport=httpx.MockTransport(handle))
            client = MinerUClient("secret-token", http_client=http_client)

            job = client.submit_file(source, data_id="book-123")

        self.assertEqual(job.batch_id, "batch-123")
        self.assertEqual(job.data_id, "book-123")
        self.assertEqual([request.method for request in requests], ["POST", "PUT"])
        self.assertEqual(requests[0].headers["Authorization"], "Bearer secret-token")
        body = json.loads(requests[0].content)
        self.assertEqual(body["files"], [{"name": "source.pdf", "data_id": "book-123"}])
        self.assertEqual(requests[1].content, b"%PDF-1.7\ntextbook")

    def test_get_status_returns_progress_for_submitted_document(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                str(request.url),
                "https://mineru.net/api/v4/extract-results/batch/batch-123",
            )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": "book-123",
                                "state": "running",
                                "extract_progress": {"extracted_pages": 4, "total_pages": 10},
                            }
                        ]
                    },
                },
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handle))
        client = MinerUClient("secret-token", http_client=http_client)

        status = client.get_status(MinerUJob("batch-123", "book-123"))

        self.assertEqual(status.state, "running")
        self.assertEqual(status.extracted_pages, 4)
        self.assertEqual(status.total_pages, 10)
        self.assertFalse(status.is_terminal)

    def test_download_result_saves_completed_archive(self) -> None:
        archive = b"PK\x03\x04mineru-result"

        def handle(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "https://cdn-mineru.openxlab.org.cn/result.zip")
            return httpx.Response(200, content=archive)

        http_client = httpx.Client(transport=httpx.MockTransport(handle))
        client = MinerUClient("secret-token", http_client=http_client)
        status = MinerUStatus(
            state="done",
            result_url="https://cdn-mineru.openxlab.org.cn/result.zip",
        )

        with tempfile.TemporaryDirectory() as directory:
            destination = client.download_result(status, Path(directory))
            self.assertEqual(destination.read_bytes(), archive)
            self.assertEqual(list(Path(directory).glob("*.downloading")), [])


if __name__ == "__main__":
    unittest.main()
