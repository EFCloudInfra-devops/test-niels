# /app/backend/Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app/backend
# RUN apt-get update
# RUN apt-get install -y sqlite3
CMD ["gunicorn", "-k","uvicorn.workers.UvicornWorker", "app.main:app", "-w","2", "--bind","0.0.0.0:8000"]