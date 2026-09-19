FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Set working directory
WORKDIR /app

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

# Copy the rest of the application
COPY . .

RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

# Set environment variables for the application
ENV BROWSER_HEADLESS=0
ENV PORT=10000

CMD ["sh", "-c", "xvfb-run -a gunicorn --timeout 120 --bind 0.0.0.0:${PORT:-10000} wsgi:app"]
