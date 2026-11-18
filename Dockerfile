FROM python:3.12-slim
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
WORKDIR /app
COPY app.py pdf2epub.py ./
COPY static ./static
EXPOSE 8000
CMD ["gunicorn", "-w", "3", "-t", "900", "-b", "0.0.0.0:8000", "app:app"]
