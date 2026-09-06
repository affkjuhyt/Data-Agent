FROM python:3.11-slim
WORKDIR /app
COPY backend/services/flow_builder/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/libs/ ./libs/
COPY backend/services/flow_builder/ .
# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app

