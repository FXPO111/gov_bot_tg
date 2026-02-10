FROM python:3.11-slim

WORKDIR /app

COPY bot/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared /app/shared
COPY bot /app/bot

ENV PYTHONPATH=/app

CMD ["python", "-m", "bot.main"]
