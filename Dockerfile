FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY src/ ./src/

RUN mkdir -p data/states temp

CMD ["python", "main.py", "--mode", "schedule"]
