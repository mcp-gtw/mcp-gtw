# Share the same pinned Python 3.14 slim image between build and runtime.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS base

# Disable bytecode files and flush logs immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Build the package wheel and export production dependencies from the lockfile.
FROM base AS build

WORKDIR /build

RUN pip install --no-cache-dir --upgrade pip==26.2.1 uv==0.13.0

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv export --frozen --no-dev --no-emit-project --output-file requirements.txt \
    && uv build --wheel

# Assemble the runtime image without the source tree or build tooling.
FROM base AS runtime

# Listen on all container interfaces so the published port is reachable.
ENV GATEWAY_HOST=0.0.0.0

WORKDIR /srv

COPY --from=build /build/requirements.txt /tmp/requirements.txt
COPY --from=build /build/dist /tmp/wheels

# Verify dependency hashes, install the wheel, and remove installers and temporary files.
RUN pip install --no-cache-dir --upgrade pip==26.2.1 \
    && pip install --no-cache-dir \
        --only-binary=:all: \
        --require-hashes \
        -r /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps /tmp/wheels/*.whl \
    && pip uninstall --yes pip \
    && rm -rf /tmp/requirements.txt /tmp/wheels /usr/local/lib/python3.14/ensurepip

# Run the gateway with a fixed unprivileged UID and GID.
RUN groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid gateway --no-create-home gateway \
    && find / -xdev -type f -perm /6000 -exec chmod a-s {} + \
    && rm -f /usr/bin/mount /usr/bin/umount /usr/bin/nsenter /usr/bin/infocmp \
        /usr/bin/su /usr/bin/chfn /usr/bin/chsh /usr/bin/newgrp \
        /usr/bin/passwd /usr/bin/gpasswd /usr/sbin/runuser

USER gateway

EXPOSE 8000

# Use the bundled runner, which applies the gateway transport limits.
CMD ["python", "-m", "mcpgtw.main"]
