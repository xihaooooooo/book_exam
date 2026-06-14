"""MinerU OCR 客户端：利用 MinerU API 将扫描版 PDF 转为 Markdown 文本。

用法：
    client = MinerUClient(token="...")
    markdown_text = client.parse_pdf("book.pdf")
"""

import os
import tempfile
import time
import zipfile
import io
from pathlib import Path

import requests


class MinerUClient:
    """MinerU 精准解析 API（vlm 模型）—— 异步提交 + 轮询。"""

    BASE = "https://mineru.net/api/v4"
    PAGE_LIMIT = 200  # 单次最多 200 页

    def __init__(self, token: str):
        self.token = token
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

    def parse_pdf(self, pdf_path: str) -> str:
        """解析整个 PDF，返回合并后的 Markdown。自动处理超过 200 页的拆分。"""
        import fitz
        doc = fitz.open(pdf_path)
        total_pages = doc.page_count
        doc.close()

        if total_pages <= self.PAGE_LIMIT:
            return self._parse_single(pdf_path)

        # 拆分上传
        parts = self._split_pdf(pdf_path, total_pages)
        results = []
        for i, part_path in enumerate(parts, 1):
            print(f"  [MinerU] 处理第 {i}/{len(parts)} 部分 ({self._page_count(part_path)} 页)...")
            md = self._parse_single(part_path)
            results.append(md)
            os.remove(part_path)  # 清理临时文件

        return "\n\n".join(results)

    def _parse_single(self, file_path: str) -> str:
        """上传单个文件，轮询直到完成，返回 Markdown。"""
        # 1. 申请上传链接 + 提交任务
        file_name = os.path.basename(file_path)
        batch_id, upload_url = self._request_upload(file_name)

        # 2. PUT 上传文件
        with open(file_path, "rb") as f:
            res = requests.put(upload_url, data=f)
            if res.status_code != 200:
                raise RuntimeError(f"上传失败: {res.status_code}")

        print(f"    上传完成，等待解析...")

        # 3. 轮詢结果
        full_zip_url = self._poll_batch(batch_id, file_name)
        if not full_zip_url:
            raise RuntimeError(f"解析失败: {file_name}")

        # 4. 下载 zip，提取 full.md
        return self._download_markdown(full_zip_url)

    def _request_upload(self, file_name: str) -> tuple[str, str]:
        """申请批量上传链接，返回 (batch_id, upload_url)。"""
        payload = {
            "files": [{"name": file_name}],
            "model_version": "vlm",
            "is_ocr": True,
            "language": "ch",
        }
        res = requests.post(
            f"{self.BASE}/file-urls/batch",
            headers=self._headers,
            json=payload,
        )
        data = res.json()
        if data.get("code") != 0:
            raise RuntimeError(f"申请上传失败: {data}")
        return data["data"]["batch_id"], data["data"]["file_urls"][0]

    def _poll_batch(self, batch_id: str, file_name: str,
                    poll_interval: int = 5, max_wait: int = 600) -> str | None:
        """轮詢直到任务完成，返回 full_zip_url。"""
        url = f"{self.BASE}/extract-results/batch/{batch_id}"
        elapsed = 0

        while elapsed < max_wait:
            res = requests.get(url, headers=self._headers)
            data = res.json()

            if data.get("code") != 0:
                time.sleep(poll_interval)
                elapsed += poll_interval
                continue

            for result in data.get("data", {}).get("extract_result", []):
                if result.get("file_name") != file_name:
                    continue
                state = result.get("state")
                if state == "done":
                    return result.get("full_zip_url")
                if state == "failed":
                    print(f"    解析失败: {result.get('err_msg')}")
                    return None
                if state == "running":
                    progress = result.get("extract_progress", {})
                    extracted = progress.get("extracted_pages", 0)
                    total = progress.get("total_pages", "?")
                    print(f"    解析中... {extracted}/{total} 页")

            time.sleep(poll_interval)
            elapsed += poll_interval

        print(f"    超时（{max_wait}s）")
        return None

    def _download_markdown(self, zip_url: str) -> str:
        """下载 ZIP 并提取 full.md。"""
        res = requests.get(zip_url)
        res.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            # 找 full.md 或 *_full.md
            for name in zf.namelist():
                if name.endswith("full.md"):
                    return zf.read(name).decode("utf-8")

        raise RuntimeError("ZIP 中未找到 full.md")

    def _split_pdf(self, pdf_path: str, total_pages: int) -> list[str]:
        """将 PDF 拆分为 ≤200 页的临时文件。"""
        import fitz
        doc = fitz.open(pdf_path)
        parts = []

        for start in range(0, total_pages, self.PAGE_LIMIT):
            end = min(start + self.PAGE_LIMIT, total_pages)
            # 创建子文档
            sub = fitz.open()
            sub.insert_pdf(doc, from_page=start, to_page=end - 1)

            suffix = f"_p{start + 1}-{end}"
            tmp = tempfile.NamedTemporaryFile(
                delete=False, suffix=f"{suffix}.pdf"
            )
            tmp.close()
            sub.save(tmp.name)
            sub.close()
            parts.append(tmp.name)
            print(f"    拆分: {tmp.name} (页 {start + 1}-{end})")

        doc.close()
        return parts

    def _page_count(self, file_path: str) -> int:
        import fitz
        doc = fitz.open(file_path)
        count = doc.page_count
        doc.close()
        return count
