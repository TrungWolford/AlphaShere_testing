import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
def _clean_env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value else value


client = OpenAI(api_key=_clean_env("OPENAI_API_KEY"))

ENV_PATH = ".env"


def create_empty_vector_store(store_name: str = "optibot-kb") -> str:
    """
    Chỉ tạo vector store RỖNG.

    Lý do KHÔNG upload file ở đây:
    main.py là nơi duy nhất chịu trách nhiệm upload + ghi lại
    openai_file_id vào manifest.json. Nếu script này tự upload luôn,
    manifest.json sẽ không biết openai_file_id của các file đó,
    dẫn tới việc main.py không thể xoá đúng file cũ khi có update
    (gây trùng lặp file mồ côi trên vector store).

    Luồng đúng:
        1. python scraper.py            (tuỳ chọn, để xem trước)
        2. python setup_vector_store.py (chạy 1 lần duy nhất, tạo store rỗng)
        3. python main.py                (scrape + upload TOÀN BỘ lần đầu,
                                           từ đó về sau chỉ upload phần delta)
    """
    vector_store = client.vector_stores.create(name=store_name)
    print("Vector Store ID:", vector_store.id)
    return vector_store.id


def save_vector_store_id(vs_id: str, env_path: str = ENV_PATH) -> None:
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    lines = [ln for ln in lines if not ln.strip().startswith("VECTOR_STORE_ID=")]
    lines.append(f"VECTOR_STORE_ID={vs_id}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


if __name__ == "__main__":
    vs_id = create_empty_vector_store()
    save_vector_store_id(vs_id)
    print(f"Đã lưu VECTOR_STORE_ID={vs_id} vào {ENV_PATH}")
    print("Tiếp theo: chạy 'python main.py' để scrape và upload toàn bộ dữ liệu lần đầu.")