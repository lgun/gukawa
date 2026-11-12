FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY gukawa ./gukawa
RUN pip install '.[ocr]' && useradd --create-home --uid 10001 gukawa && mkdir -p /data /secrets && chown -R gukawa:gukawa /data
USER gukawa
ENV DATA_DIR=/data INSTAGRAM_COOKIES=/secrets/instagram-cookies.txt
EXPOSE 8000
CMD ["uvicorn", "gukawa.web:app", "--host", "0.0.0.0", "--port", "8000"]
