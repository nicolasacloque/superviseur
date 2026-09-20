FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY pyproject.toml ./
COPY bacnet_sim ./bacnet_sim
RUN pip install .

RUN useradd --system --no-create-home simulator
USER simulator

CMD ["python", "-m", "bacnet_sim"]
