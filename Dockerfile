FROM python:3.9-bookworm AS build

COPY . /src
WORKDIR /src/projects/etos_suite_runner
RUN python3 setup.py bdist_wheel

FROM python:3.9-slim-bookworm

COPY --from=build /src/projects/etos_suite_runner/dist/*.whl /tmp
# hadolint ignore=DL3013
RUN pip install --no-cache-dir /tmp/*.whl && groupadd -r etos && useradd -r -m -s /bin/false -g etos etos

USER etos

LABEL org.opencontainers.image.source=https://github.com/eiffel-community/etos-suite-runner
LABEL org.opencontainers.image.authors=etos-maintainers@googlegroups.com
LABEL org.opencontainers.image.licenses=Apache-2.0

CMD ["python", "-u", "-m", "etos_suite_runner"]
