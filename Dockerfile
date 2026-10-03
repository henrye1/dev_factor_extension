FROM python:3.12-slim

# FORWARDED_ALLOW_IPS: the addresses whose X-Forwarded-* headers are trusted. "*" suits a host
# that puts its own proxy in front of the container; set the proxy's address if you know it.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    FORWARDED_ALLOW_IPS=*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hazard_ext ./hazard_ext

# run as an unprivileged user; /data is only used when STORAGE_BACKEND=local or SQLite is used
RUN useradd --create-home appuser && mkdir /data && chown appuser /data
USER appuser

EXPOSE 8000
# one worker: the sign-in throttle and the dataset cache live in the process
CMD ["sh", "-c", "uvicorn hazard_ext.web.main:create_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers"]
