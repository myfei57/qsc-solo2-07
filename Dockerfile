FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app:/app/vendor \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY . .

RUN mkdir -p var/state

EXPOSE 8080

CMD ["python", "-m", "fgd", "--config", "config/fgd.yaml", "serve", "--host", "0.0.0.0", "--port", "8080"]
