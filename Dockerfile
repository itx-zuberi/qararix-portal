FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
CMD gunicorn app:app --preload --workers 2 --threads 4 --bind 0.0.0.0:$PORT
