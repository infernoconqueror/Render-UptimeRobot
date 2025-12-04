FROM python:3.11-slim

# 1. Install Chrome, FFmpeg, and system tools
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    ffmpeg \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# 2. Setup App
WORKDIR /app
COPY . /app

# 3. Install Python Dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 4. Run the Bot
CMD ["python", "bot.py"]
