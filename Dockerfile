FROM python:3.9-slim

WORKDIR /app

# Add build argument to invalidate cache
ARG CACHEBUST=1

# Copy requirements first to leverage Docker cache
COPY requirements.txt /app/
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt google-cloud-storage

# Copy application code
COPY . /app/

# Create podcast directory
RUN mkdir -p /tmp/episodes

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Expose port
EXPOSE 8080

# Run the application
CMD ["uvicorn", "podcast_ai:app", "--host", "0.0.0.0", "--port", "8080"]