"""
cleanup_files.py — xoá TOÀN BỘ file trong OpenAI Storage > Files.

⚠️ CẢNH BÁO: script này xoá vĩnh viễn, không hoàn tác được.
Chỉ chạy khi bạn chắc chắn các file trong Files đều là rác từ project
này (vd: file .md do scraper.py tạo ra), không có file nào khác quan
trọng nằm chung tài khoản OpenAI.

Cách dùng:
    python cleanup_files.py            # liệt kê trước, hỏi xác nhận
    python cleanup_files.py --yes      # xoá luôn không hỏi
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv
from openai import OpenAI
import os

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def list_all_files():
    files = []
    after = None
    while True:
        page = client.files.list(after=after, limit=100) if after else client.files.list(limit=100)
        files.extend(page.data)
        if not getattr(page, "has_more", False):
            break
        after = page.data[-1].id
    return files


def main():
    files = list_all_files()

    if not files:
        print("Không có file nào trong Storage > Files.")
        return

    total_bytes = sum(f.bytes for f in files)
    print(f"Tìm thấy {len(files)} file, tổng dung lượng ~{total_bytes / 1024:.1f} KB")
    for f in files[:10]:
        print(f"  - {f.id}  {f.filename}  ({f.bytes} bytes)")
    if len(files) > 10:
        print(f"  ... và {len(files) - 10} file khác")

    if "--yes" not in sys.argv:
        confirm = input(f"\nXác nhận xoá TOÀN BỘ {len(files)} file này? (gõ 'yes' để xác nhận): ")
        if confirm.strip().lower() != "yes":
            print("Huỷ, không xoá gì cả.")
            return

    deleted, failed = 0, 0
    for f in files:
        try:
            client.files.delete(f.id)
            deleted += 1
        except Exception as e:
            print(f"Lỗi xoá {f.id}: {e}")
            failed += 1

    print(f"\nĐã xoá {deleted} file, thất bại {failed} file.")


if __name__ == "__main__":
    main()