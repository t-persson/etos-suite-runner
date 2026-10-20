FROM python:3.13-trixie AS build

COPY . /src
WORKDIR /src/projects/etos_suite_runner
RUN pip install --no-cache-dir build==1.3.0 && python3 -m build

FROM python:3.13-slim-trixie

ARG BUILD_REVISION
RUN test -n "$BUILD_REVISION" || (echo "BUILD_REVISION must be supplied" >&2; exit 1)
ENV BUILD_REVISION=$BUILD_REVISION

COPY --from=build /src/projects/etos_suite_runner/dist/*.whl /tmp
# hadolint ignore=DL3013
RUN pip install --no-cache-dir /tmp/*.whl && groupadd -r etos && useradd -r -m -s /bin/false -g etos etos

USER etos

LABEL org.opencontainers.image.source=https://github.com/eiffel-community/etos-suite-runner
LABEL org.opencontainers.image.authors=etos-maintainers@googlegroups.com
LABEL org.opencontainers.image.licenses=Apache-2.0

CMD ["python", "-u", "-m", "etos_suite_runner"]
