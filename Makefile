PYTHON ?= python
RESOURCE_GROUP ?= rg-aisaas-prod-swc
CLUSTER ?= aks-aisaas-prod-swc
LZ_OUTPUTS ?= ../lz-outputs.txt
OVERLAYS := starter business enterprise
K8S_VERSION ?= 1.36.0
CRD_SCHEMAS := https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json

KUBECTL ?= kubectl
KUBECONFORM ?= kubeconform
KUSTOMIZATIONS := k8s/overlays/starter k8s/overlays/business k8s/overlays/enterprise k8s/platform

.PHONY: install lint type test budget check lz-env validate-infra preview deploy destroy stop start kube-validate

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:
	ruff check . && ruff format --check .

type:
	mypy

test:
	pytest -q

budget:
	pytest -q tests/test_budget.py

check: lint type test

lz-env:
	@export MSYS_NO_PATHCONV=1; grep -E '^[a-z][A-Za-z]+=' $(LZ_OUTPUTS) | while IFS='=' read -r key value; do \
	  name=$$(echo "$$key" | sed -E 's/([a-z0-9])([A-Z])/\1_\2/g' | tr '[:lower:]' '[:upper:]'); \
	  azd env set "$$name" "$$(echo "$$value" | tr -d '"')"; \
	done

validate-infra:
	az bicep build --file infra/main.bicep --stdout --only-show-errors > /dev/null && az bicep lint --file infra/main.bicep --only-show-errors

preview:
	az deployment group what-if -g $(RESOURCE_GROUP) -f infra/main.bicep -p infra/main.bicepparam --only-show-errors

deploy:
	az deployment group create -g $(RESOURCE_GROUP) -f infra/main.bicep -p infra/main.bicepparam --only-show-errors

destroy:
	azd down --force --purge

stop:
	az aks stop -g $(RESOURCE_GROUP) -n $(CLUSTER)

start:
	az aks start -g $(RESOURCE_GROUP) -n $(CLUSTER)

kube-validate:
	@for dir in $(KUSTOMIZATIONS); do \
	  $(KUBECTL) kustomize --load-restrictor LoadRestrictionsNone $$dir | $(KUBECONFORM) -strict -summary -kubernetes-version $(K8S_VERSION) \
	    -schema-location default -schema-location '$(CRD_SCHEMAS)' || exit 1; \
	done
