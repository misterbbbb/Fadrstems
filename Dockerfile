FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY streamlit_app.py .
ENV PORT=8501
EXPOSE 8501
CMD ["bash","-lc","streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT}"]
