FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY mtglogger/__init__.py ./mtglogger/
RUN pip install --no-cache-dir '.[ocr]'
COPY mtglogger ./mtglogger
COPY evaluation ./evaluation
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn", "mtglogger.main:app", "--host", "0.0.0.0", "--port", "8000"]
