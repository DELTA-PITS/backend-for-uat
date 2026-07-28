FROM python:3.12.8-bookworm
LABEL authors="Amal Khairunnisa"

ARG BUILD_DATE
ENV BUILD_DATE=$BUILD_DATE


# Combine apt-get commands to reduce layers
RUN apt-get update -y && \
    apt-get upgrade -y && \
    apt-get dist-upgrade -y && \
    apt-get install -y --no-install-recommends git curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONPATH=/home/runner/work/V2-Backend/V2-Backend/src
ENV BASE_DIR=/home/runner/work/V2-Backend/V2-Backend/

WORKDIR ${BASE_DIR}

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create and activate virtual environment
RUN python -m venv .venv
ENV APP_NAME="ost-clarin-skg"
ENV PATH="/home/runner/work/V2-Backend/V2-Backend/.venv/bin:$PATH"

# Copy the application into the container.
COPY conf/ ./conf/
COPY src/ ./src/
COPY tests/ ./tests/
COPY pyproject.toml .
COPY README.md .
COPY uv.lock .


# Install dependencies

RUN uv sync --frozen --no-cache
RUN mkdir -p logs resources/data/files-storage/registered resources/data/files-storage/verified resources/data/db
EXPOSE 41012
# Run the application.
CMD ["python", "-m", "trustmark.main"]

#CMD ["tail", "-f", "/dev/null"]
