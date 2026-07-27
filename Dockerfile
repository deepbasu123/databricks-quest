# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Pinned tool versions (verified available for linux arm64 + amd64, 2026-07-27).
ARG DATABRICKS_CLI_VERSION=1.9.0
ARG TERRAFORM_VERSION=1.9.8

# System deps: psql (hard requirement in deploy.sh), plus fetch/unzip tooling.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates unzip git postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Databricks CLI (official setup-cli install script, pinned). Installs to
# /usr/local/bin/databricks. Arch derived at runtime via dpkg so this works with
# both the legacy builder (where TARGETARCH is empty) and BuildKit.
RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/dbcli.zip \
      "https://github.com/databricks/cli/releases/download/v${DATABRICKS_CLI_VERSION}/databricks_cli_${DATABRICKS_CLI_VERSION}_linux_${ARCH}.zip" \
    && unzip -q /tmp/dbcli.zip -d /tmp/dbcli \
    && mv /tmp/dbcli/databricks /usr/local/bin/databricks \
    && chmod +x /usr/local/bin/databricks \
    && rm -rf /tmp/dbcli /tmp/dbcli.zip \
    && databricks --version

# Terraform (pinned). DATABRICKS_TF_EXEC_PATH points DAB at this binary so the
# bundle deploy never tries to download Terraform (which has failed on expired
# signing keys in restricted environments).
RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/tf.zip \
      "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${ARCH}.zip" \
    && unzip -q /tmp/tf.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/terraform \
    && rm -f /tmp/tf.zip \
    && terraform version
ENV DATABRICKS_TF_EXEC_PATH=/usr/local/bin/terraform

# App source. app/static/ is committed (prebuilt frontend) and shipped as-is;
# Node is intentionally absent so nothing rebuilds the frontend.
WORKDIR /quest
COPY . /quest
RUN chmod +x /quest/deploy.sh /quest/docker-deploy.sh

ENTRYPOINT ["/quest/docker-deploy.sh"]
