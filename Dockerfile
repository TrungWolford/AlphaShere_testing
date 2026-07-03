FROM python:3.11-slim

WORKDIR /app

# Cài dependencies trước để tận dụng Docker layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ source code
COPY scraper.py main.py ./

# Chạy 1 lần và thoát (exit 0 nếu thành công, non-zero nếu lỗi)
CMD ["python", "main.py"]
