FROM python:3.11-slim

# System deps for Playwright/Chromium
RUN apt-get update && apt-get install -y --no-install-recommends     libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2     libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3     libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 libpango-1.0-0 libcairo2     libxshmfence1 fonts-liberation libx11-xcb1 libx11-6 libxext6 libxrender1     xdg-utils wget gnupg ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium for Playwright (and its deps)
RUN python -m playwright install --with-deps chromium

COPY streamlit_app.py .

ENV PORT=8501
EXPOSE 8501

CMD ["bash", "-lc", "streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT}"]
