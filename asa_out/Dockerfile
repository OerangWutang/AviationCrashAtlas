FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev gdal-bin libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# Install pinned production dependencies from lockfile.
# This layer is cached as long as requirements.lock doesn't change.
# Never install from floating pyproject.toml dependency ranges in production.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Install the atlas package itself (no-deps because all deps are already locked).
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir --no-deps -e .

COPY migrations/ migrations/
COPY alembic.ini .

RUN useradd -m atlas
USER atlas

EXPOSE 8000

# The 'atlas' command is installed via pyproject.toml [project.scripts]
CMD ["atlas", "serve"]
