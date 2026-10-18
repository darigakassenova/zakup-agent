FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bins.py ./
COPY sql ./sql
COPY src ./src
COPY data/reference ./data/reference

# Приложение работает не от root
RUN useradd --create-home app && mkdir -p /app/eval && chown -R app /app
USER app

CMD ["python", "src/scheduler.py"]
