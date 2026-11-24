# Build from the folder that contains BOTH repositories:
#   workspace/
#     payload-doctor/
#     integrx/
# docker compose (in integrx/) sets the build context to ".." for this reason.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl jq \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY payload-doctor /src/payload-doctor
RUN pip install --no-cache-dir /src/payload-doctor

COPY integrx/pyproject.toml integrx/README.md /app/
COPY integrx/src /app/src
RUN pip install --no-cache-dir /app

COPY integrx/runbooks /app/runbooks
COPY integrx/scripts /app/scripts
COPY integrx/partner_sim /app/partner_sim

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "integrx.api:app", "--host", "0.0.0.0", "--port", "8080"]
