FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app/ app/
COPY run.py .

# Non-root user
RUN useradd -m walletintel
USER walletintel

EXPOSE 8000

CMD ["python", "run.py"]
