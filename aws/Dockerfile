# The kerykeion CLI as an AWS Lambda container image, behind API Gateway.
#
# Build from the repository root (the library and the CLI are installed from source):
#   docker buildx build --platform linux/arm64 -f aws/Dockerfile -t kerykeion-api .
#
# The wheel bundles only the base ephemeris tier (1850-2150). A birth chart
# before 1850 needs a wider tier, so its files are baked in at build time: a
# Lambda cannot download them per request (read-only image, sealed network mode).
#   EPHEMERIS_TIER=medium    1550-2650, ~90 MB   (default)
#   EPHEMERIS_TIER=base      1850-2150, nothing downloaded
ARG PYTHON_VERSION=3.13
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION}

ARG EPHEMERIS_TIER=medium

ENV LIBEPHEMERIS_DATA_DIR=/opt/libephemeris \
    LIBEPHEMERIS_PRECISION=${EPHEMERIS_TIER} \
    KERYKEION_CLI_FORMAT=json \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml README.md LICENSE NOTICE /src/
COPY licenses /src/licenses
COPY kerykeion /src/kerykeion
COPY cli /src/cli
RUN pip install --no-cache-dir /src /src/cli && rm -rf /src

RUN if [ "${EPHEMERIS_TIER}" != "base" ]; then \
      python -c "from libephemeris.download import download_leb2_for_tier as d; d('${EPHEMERIS_TIER}', groups=None, show_progress=False)" ; \
    fi

COPY aws/handler/kerykeion_api.py ${LAMBDA_TASK_ROOT}/

# Fail the build, not the first request, if the image cannot cast a chart.
RUN cd /tmp && PYTHONPATH=${LAMBDA_TASK_ROOT} python -c "import kerykeion_api, json; \
r = kerykeion_api.handler({'httpMethod': 'POST', 'path': '/now', 'body': json.dumps({'lat': 51.5, 'lng': -0.13, 'tz': 'Europe/London', 'offline': True})}); \
assert r['statusCode'] == 200, r" \
    && rm -rf /tmp/*

CMD ["kerykeion_api.handler"]
