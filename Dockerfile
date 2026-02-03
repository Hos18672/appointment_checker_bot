# Use Debian with Python 3.12 (latest stable, fully supported)
FROM python:3.12-bullseye

# Install necessary dependencies for Chromium and image processing
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    libpng-dev \
    libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for Selenium
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application files
COPY . .

# Create directory for screenshots/logs
RUN mkdir -p /app/logs

# Run the bot
CMD ["python", "bot.py"]
