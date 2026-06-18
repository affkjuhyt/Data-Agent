FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py extract_prompt.txt ./
CMD ["python", "-u", "main.py"]
