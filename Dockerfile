FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Set working directory
WORKDIR /app

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

# Copy the rest of the application
COPY . .

# Ensure the correct port is exposed for Render
EXPOSE 10000

# Set environment variables for the application
ENV BROWSER_HEADLESS=1
ENV PORT=10000

CMD ["sh", "-c", "gunicorn --timeout 120 --bind 0.0.0.0:${PORT:-10000} wsgi:app"]
