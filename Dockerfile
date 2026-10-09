# The whole demo in one image: the API, the built console, Postgres and Qdrant.
# It is written for a Hugging Face Docker Space, which runs the container as user 1000
# and shows whatever listens on port 7860. Any host that runs one container will do.

FROM node:22-slim AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci
COPY console/ ./
RUN npm run build

FROM qdrant/qdrant:latest AS qdrant

FROM python:3.13-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql libunwind8 ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 user

COPY --from=qdrant --chown=1000:1000 /qdrant /qdrant

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/home/user/.cache/fastembed
WORKDIR /home/user/app

# Dependencies first, so a change to the code does not install them all again.
COPY --chown=user pyproject.toml README.md ./
COPY --chown=user agent/__init__.py agent/__init__.py
COPY --chown=user api/__init__.py api/__init__.py
COPY --chown=user data/__init__.py data/__init__.py
COPY --chown=user evals/__init__.py evals/__init__.py
RUN pip install --no-cache-dir --user -e ".[hosted]"

# The embedding model is downloaded once here, so a cold start never waits for it.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

COPY --chown=user agent/ agent/
COPY --chown=user api/ api/
COPY --chown=user data/ data/
COPY --chown=user evals/ evals/
COPY --chown=user mcp_server/ mcp_server/
COPY --chown=user docs/EVALS.md docs/EVALS.md
COPY --chown=user deploy/start.sh deploy/start.sh
# A checkout on Windows can leave Windows line endings in the script, and bash cannot run those.
RUN sed -i 's/\r$//' deploy/start.sh
COPY --chown=user --from=console /console/dist console/dist

EXPOSE 7860
CMD ["bash", "deploy/start.sh"]
