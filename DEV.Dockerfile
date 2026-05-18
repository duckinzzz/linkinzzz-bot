FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    unzip \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONUNBUFFERED=1

CMD ["python", "run.py"]