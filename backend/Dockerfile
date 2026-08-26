FROM python:3.11-slim

WORKDIR /app

# Устанавливаем FFmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ПРАВИЛЬНАЯ команда запуска (обязательно в кавычках!)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]