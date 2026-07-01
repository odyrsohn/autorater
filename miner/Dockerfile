FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY miner/ ./miner/
RUN useradd --no-create-home miner
USER miner
ENTRYPOINT ["python", "-m", "miner.worker"]
