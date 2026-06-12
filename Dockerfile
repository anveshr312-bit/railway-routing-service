FROM python:3.12-slim

# Install system dependencies needed for spatial libraries
# spatialindex is needed for rtree
RUN apt-get update && apt-get install -y \
    build-essential \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create a non-root user with UID 1000 (Required by Hugging Face)
RUN useradd -m -u 1000 user

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Change ownership of the app directory to the non-root user
RUN chown -R user:user /app

# Switch to the non-root user
USER user

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV RAILWAYS_GPKG=/app/railways.gpkg

# Expose Hugging Face default port
EXPOSE 7860

# Run uvicorn on port 7860
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
