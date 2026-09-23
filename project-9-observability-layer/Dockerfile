# Deliberately minimal: the exporter is standard library only, so there is
# nothing to pip install and no lockfile to drift.
FROM python:3.12-slim

WORKDIR /app
COPY obs/ ./obs/

EXPOSE 9109

# Unbuffered, or JSON log lines sit in a pipe buffer and arrive in bursts --
# which makes a log timeline useless exactly when you need one.
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "obs.server", "--port", "9109"]
