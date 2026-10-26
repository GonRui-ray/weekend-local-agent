FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY space/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY agent/ ./
COPY space/app.py ./app.py
COPY NOTICE.md ./NOTICE.md
COPY --from=frontend /frontend/dist/ ./ui/
ENV HOST=0.0.0.0 PORT=7860 GRADIO_ANALYTICS_ENABLED=False PYTHONDONTWRITEBYTECODE=1
EXPOSE 7860
CMD ["python", "app.py"]
