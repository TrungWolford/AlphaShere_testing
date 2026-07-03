from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from scraper import scrape_articles

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
VECTOR_STORE_ID = os.getenv("VECTOR_STORE_ID")

MANIFEST_PATH = Path("manifest.json")

# Nếu số file "added" >= ngưỡng này, dùng batch_upload_files() (nhanh, xử lý
# song song ở server). Nếu ít hơn, upload từng file cũng đủ nhanh và đơn giản.
BATCH_THRESHOLD = 5


# ============================================================
# Manifest helpers
# ============================================================

def load_manifest() -> dict:
    """
    scraper.scrape_articles() already writes manifest.json before returning,
    so we just read it back here to enrich it with openai_file_id values.
    """
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest: dict) -> None:
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


# ============================================================
# Vector store helpers
# ============================================================

def delete_vector_store_file(file_id: str | None) -> None:
    """
    Best-effort delete. We don't want the whole job to fail just because
    one stale file_id no longer exists on OpenAI's side.
    """
    if not file_id:
        return
    try:
        client.vector_stores.files.delete(
            vector_store_id=VECTOR_STORE_ID,
            file_id=file_id,
        )
    except Exception as e:
        print(f"Warn: could not delete vector store file {file_id}: {e}")


def upload_vector_store_file(filepath: str) -> str:
    """
    Upload 1 file kèm poll riêng lẻ. Dùng cho số lượng nhỏ (vd: "updated"),
    vì đơn giản và đủ nhanh khi chỉ có vài file.
    """
    with open(filepath, "rb") as f:
        uploaded = client.vector_stores.files.upload_and_poll(
            vector_store_id=VECTOR_STORE_ID,
            file=f,
        )
    return uploaded.id


def batch_upload_files(article_ids: list[str], manifest: dict) -> None:
    """
    Upload NHIỀU file cùng lúc, xử lý song song ở server thay vì tuần tự.

    Cách làm:
      1. Tạo Files object cho từng file qua client.files.create() — bước này
         KHÔNG cần poll (upload xong là có file.id ngay), nên map được
         chính xác file_id <-> article_id.
      2. Gộp toàn bộ file_id vào 1 lệnh file_batches.create_and_poll() —
         chỉ CHỜ (poll) MỘT LẦN cho cả batch, thay vì chờ từng file.

    Đây là lý do chính giúp nhanh hơn hẳn so với gọi
    upload_vector_store_file() trong vòng lặp cho hàng trăm file.
    Cập nhật trực tiếp manifest["openai_file_id"] cho từng article_id.
    """
    if not article_ids:
        return

    file_id_by_article: dict[str, str] = {}
    for article_id in article_ids:
        filepath = manifest[article_id]["file"]
        with open(filepath, "rb") as f:
            file_obj = client.files.create(file=f, purpose="assistants")
        file_id_by_article[article_id] = file_obj.id

    client.vector_stores.file_batches.create_and_poll(
        vector_store_id=VECTOR_STORE_ID,
        file_ids=list(file_id_by_article.values()),
    )

    for article_id, file_id in file_id_by_article.items():
        manifest[article_id]["openai_file_id"] = file_id


# ============================================================
# Sync logic
# ============================================================

def sync() -> None:
    if not VECTOR_STORE_ID:
        raise SystemExit(
            "VECTOR_STORE_ID chưa được thiết lập trong .env / biến môi trường. "
            "Hãy chạy setup_vector_store.py trước."
        )

    # 1. Scrape + local delta detection (writes manifest.json itself)
    result = scrape_articles()
    manifest = load_manifest()

    # 2. Updated articles: xoá file cũ trên vector store trước, rồi upload lại.
    #    Số lượng "updated" mỗi ngày thường nhỏ nên upload từng file là đủ.
    for article_id in result["updated"]:
        entry = manifest[article_id]
        delete_vector_store_file(entry.get("openai_file_id"))
        entry["openai_file_id"] = upload_vector_store_file(entry["file"])

    # 3. Added articles: dùng batch upload nếu số lượng lớn (vd: lần chạy
    #    đầu tiên với hàng trăm bài), nếu ít thì upload từng file cho đơn giản.
    added_ids = result["added"]
    if len(added_ids) >= BATCH_THRESHOLD:
        batch_upload_files(added_ids, manifest)
    else:
        for article_id in added_ids:
            entry = manifest[article_id]
            entry["openai_file_id"] = upload_vector_store_file(entry["file"])

    # 4. Deleted articles: xoá khỏi vector store bằng openai_file_id cũ
    #    (deleted_entries được scraper.py giữ lại riêng vì các bài này
    #    không còn nằm trong manifest mới)
    for article_id, old_entry in result["deleted_entries"].items():
        delete_vector_store_file(old_entry.get("openai_file_id"))

    # 5. Lưu lại manifest với openai_file_id mới nhất
    save_manifest(manifest)

    summary = {
        "total_articles_seen": result["total_articles_seen"],
        "added": len(result["added"]),
        "updated": len(result["updated"]),
        "skipped": len(result["skipped"]),
        "deleted": len(result["deleted"]),
        "errors": len(result["errors"]),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    sync()