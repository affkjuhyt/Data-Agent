FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py schema_hash.py snowflake_metadata.py llm_describe.py embed.py describe_prompt.txt ./
# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app
CMD ["python", "-u", "main.py"]
