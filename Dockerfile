FROM node:22.23.4-alpine3.23 AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

FROM python:3.12.15-slim-bookworm
ARG KUBECTL_VERSION=v1.36.5
ARG KUBECTL_SHA256=33bc88a24c3b09cf55bfd59e6ca977881b61fc3f25ab65bc80c65b430248648e
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -c "import urllib.request as u; u.urlretrieve('https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl', '/usr/local/bin/kubectl')" \
 && echo "${KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod 0755 /usr/local/bin/kubectl \
 && groupadd --gid 10001 gateway \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin gateway
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY app/ app/
COPY ops/ ops/
COPY k8s/ k8s/
COPY --from=console /console/dist/ console/dist/
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
