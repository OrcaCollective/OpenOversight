FROM python:3.13.2-slim AS base
ARG IS_PROD
ENV DEBIAN_FRONTEND noninteractive
ENV PIP_NO_CACHE_DIR=1
ENV POETRY_NO_INTERACTION=1
ENV POETRY_VIRTUALENVS_CREATE=0
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /usr/src/app

# Install packages depending on ENV arg from docker-compose
ARG PACKAGES_TO_INSTALL="gcc libpq-dev libjpeg62-turbo-dev libsqlite3-0 zlib1g-dev cargo"
RUN apt-get update && \
    apt-get install -y -qq --no-install-recommends $PACKAGES_TO_INSTALL && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Add runtime dependencies to base image
RUN pip3 install poetry~=2.1.1
COPY pyproject.toml poetry.lock ./
RUN if [ "$IS_PROD" = "true" ]; then \
        poetry install --only main --no-root; \
    else \
        poetry install --no-root; \
        playwright install --with-deps chromium; \
    fi

# Setup application
COPY create_db.py .
COPY OpenOversight OpenOversight

CMD if [ "$IS_PROD" = "true" ]; then \
        gunicorn -w 4 -b 0.0.0.0:3000 OpenOversight.app:app; \
    else \
        flask run --host=0.0.0.0 --port=3000; \
    fi
