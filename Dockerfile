# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Install system dependencies (like gcc, as specified in Nixpacks setup)
RUN apt-get update && apt-get install -y gcc

# Upgrade pip, setuptools, and wheel
RUN python3 -m pip install --upgrade pip setuptools wheel

# Install Python dependencies from requirements.txt
RUN pip install -r requirements.txt

# Run the application with gunicorn
CMD ["gunicorn", "app:app"]
