# Use a lightweight and stable Python version
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Install libraries required for metadata handling
RUN pip install --no-cache-dir piexif pytz pillow

# Copy the Python script and tests into the container
COPY metadata_updater.py .
COPY tests tests

# Default command when the container starts
CMD ["python", "metadata_updater.py"]