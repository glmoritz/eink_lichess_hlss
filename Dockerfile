FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies for Pillow and fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    libpng-dev \
    libfreetype6-dev \
    fonts-dejavu-core \
    iputils-ping \
    wget \
    libcairo2 \     
    libpango-1.0-0 \ 
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash hlss
WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy application code
COPY --chown=hlss:hlss src/ ./src/
COPY --chown=hlss:hlss alembic/ ./alembic/
COPY --chown=hlss:hlss alembic.ini ./

# Switch to non-root user
USER hlss

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "hlss.main:app", "--host", "0.0.0.0", "--port", "8000"]
