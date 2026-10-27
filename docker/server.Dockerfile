FROM python:3.13-slim AS preview-build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY src/display/IT8951 /app/src/display/IT8951
COPY src/server/preview.py /app/src/server/preview.py
RUN python src/server/preview.py --build /out/libpage-preview.so

FROM python:3.13-slim
WORKDIR /app
COPY src/server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --create-home epaper \
    && mkdir -p /data && chown epaper:epaper /data
COPY src/server /app/src/server
COPY src/scheduler/wake_plan.py /app/src/scheduler/wake_plan.py
COPY src/display/IT8951 /app/src/display/IT8951
COPY --from=preview-build /out/libpage-preview.so /app/build/display/libpage-preview.so
USER epaper
ENV DATABASE=/data/server.sqlite3 PYTHONUNBUFFERED=1
EXPOSE 8088
CMD ["gunicorn", "--bind", "0.0.0.0:8088", "--workers", "2", "--threads", "2", "--timeout", "30", "--access-logfile", "-", "src.server:create_app()"]
