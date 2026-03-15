FROM python:3.11-slim

WORKDIR /ecoscout

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .

COPY app/ app/

EXPOSE 8080

WORKDIR /ecoscout/app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
