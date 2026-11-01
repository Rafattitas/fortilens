FROM python:3.12-slim@sha256:1148dbb014615300fa7e713b2f94f550c0d1c69780c0805f84f093ceee34dd0b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /app
COPY management/requirements.txt /app/management/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r management/requirements.txt
COPY management /app/management
COPY portable /app/portable
USER 10001:10001
EXPOSE 3030
ENTRYPOINT ["python", "-m", "management.app"]
