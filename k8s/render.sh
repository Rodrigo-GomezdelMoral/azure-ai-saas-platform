#!/usr/bin/env bash
# Writes the deploy-time kustomize wrappers under k8s/.generated: the image, the service-account client ids, the KEDA
# identity and the settings the committed manifests carry as placeholders. Inputs are environment variables: the
# deployment outputs (make deployment-env), the azd environment and TAG. Nothing environment-specific is committed.
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${TAG:-$(git rev-parse --short HEAD)}"
export GATEWAY_IMAGE="${REGISTRY_LOGIN_SERVER:?}/gateway:${TAG}"
export KEYVAULT_URL="${VAULT_URL:?}" OIDC_ISSUER="${OIDC_ISSUER_URL:?}"
export APPLICATIONINSIGHTS_CONNECTION_STRING="${APP_INSIGHTS_CONNECTION_STRING:-}"
export REDIS_PORT="${REDIS_PORT:-10000}" COSMOS_DATABASE="${COSMOS_DATABASE:-saas}" SHARED_INDEX="${SHARED_INDEX:-regulations}"
export SIGNING_KEY_NAME="${SIGNING_KEY_NAME:-credential-signing}" ONBOARD_WORKDIR=/tmp/onboard
KEYS="ENTRA_TENANT_ID API_AUDIENCE GATEWAY_CLIENT_ID GATEWAY_IDENTITY_ID GATEWAY_IMAGE REDIS_HOST REDIS_PORT COSMOS_ENDPOINT
COSMOS_DATABASE COSMOS_ACCOUNT_ID SERVICEBUS_NAMESPACE EVENTGRID_ENDPOINT FOUNDRY_ENDPOINT EMBEDDING_DEPLOYMENT SEARCH_ENDPOINT
SHARED_INDEX KEYVAULT_URL SIGNING_KEY_NAME UPSTREAM_URL UPSTREAM_AUDIENCE OIDC_ISSUER ONBOARD_WORKDIR APPLICATIONINSIGHTS_CONNECTION_STRING"

for name in starter platform; do
  mkdir -p "k8s/.generated/$name"
  for key in $KEYS; do
    if [ "$key" = APPLICATIONINSIGHTS_CONNECTION_STRING ]; then echo "$key=${!key}"; else echo "$key=${!key:?missing $key}"; fi
  done > "k8s/.generated/$name/settings.env"
  "${PYTHON:-python3}" - "$name" "${GATEWAY_IMAGE%:*}" "$TAG" "${GATEWAY_CLIENT_ID:?}" "${ONBOARD_CLIENT_ID:?}" "${FUNCTION_CLIENT_ID:?}" <<'PY' > "k8s/.generated/$name/kustomization.yaml"
import json, sys
name, image, tag, gateway, onboard, function = sys.argv[1:7]
annotation = "/metadata/annotations/azure.workload.identity~1client-id"
def account(sa, client):
    return {"target": {"kind": "ServiceAccount", "name": sa}, "patch": json.dumps([{"op": "replace", "path": annotation, "value": client}])}
keda = {"target": {"kind": "TriggerAuthentication", "name": "servicebus-workload-identity"},
        "patch": json.dumps([{"op": "replace", "path": "/spec/podIdentity/identityId", "value": function}])}
patches = [account("gateway", gateway)] if name == "starter" else [account("meter-drain", gateway), account("onboarding", onboard), keda]
wrapper = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
           "resources": ["../../overlays/starter" if name == "starter" else "../../platform"],
           "images": [{"name": "gateway", "newName": image, "newTag": tag}],
           "generatorOptions": {"disableNameSuffixHash": True},
           "configMapGenerator": [{"name": "gateway-settings", "behavior": "replace", "envs": ["settings.env"]}],
           "patches": patches}
print(json.dumps(wrapper, indent=2))
PY
  echo "rendered k8s/.generated/$name"
done
