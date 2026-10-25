# VERSION 同時傳給 Compose 的 image tag 與 APP_VERSION，預設為 v1。
VERSION ?= v1
export VERSION
PYTHON ?= python
COMPOSE = docker compose -f deploy/compose/docker-compose.yml
# 固定練習叢集名稱與操作目標，避免依目前 context 操作到別的叢集。
KIND_CLUSTER := fab
KUBE_CONTEXT := kind-$(KIND_CLUSTER)
KUBECTL = kubectl --context $(KUBE_CONTEXT)
N ?= 3
# 使用者的 kind 節點為 Linux AMD64；匯出單一平台避免缺少其他平台的 digest。
IMAGE_PLATFORM ?= linux/amd64
# 本專案已使用 Docker，明確指定 provider 避免誤選其他容器引擎。
export KIND_EXPERIMENTAL_PROVIDER = docker

.PHONY: help build up down logs test ps cluster cluster-down load deploy status watch scale kill-pod
.DEFAULT_GOAL := help

help:
	@echo "Phase 1 - Docker"
	@echo "  make build [VERSION=v1]  Build both service images"
	@echo "  make up                  Build and start Compose; wait for healthchecks"
	@echo "  make down                Stop and remove Compose containers"
	@echo "  make logs                Follow all service logs (Ctrl+C to exit)"
	@echo "  make test [PYTHON=python] Run local pytest"
	@echo "  make ps                  Show container status"
	@echo "Phase 2 - Kubernetes (D3: local cluster)"
	@echo "  make cluster             Create fab kind cluster; wait for three Ready nodes"
	@echo "  make cluster-down        Delete the fab kind cluster and its workloads"
	@echo "Phase 2 - Kubernetes (D4: application deployment)"
	@echo "  make load [VERSION=v1]   Export platform-specific images and load into kind"
	@echo "  make deploy              Apply deploy/k8s (initial application version v1)"
	@echo "  make status              Show fab pods, services and deployments"
	@echo "  make watch               Watch fab pods and their nodes (Ctrl+C to exit)"
	@echo "  make scale [N=3]         Change the desired number of API pods"
	@echo "  make kill-pod            Delete one random running API pod (WSL/Bash)"

build:
	$(COMPOSE) build tool-api tool-simulator

up:
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs --follow --tail=100

test:
	$(PYTHON) -m pytest

ps:
	$(COMPOSE) ps

cluster:
	kind create cluster --name $(KIND_CLUSTER) --config deploy/kind/kind-config.yaml --wait 180s
	kubectl --context $(KUBE_CONTEXT) wait --for=condition=Ready nodes --all --timeout=180s
	kubectl --context $(KUBE_CONTEXT) get nodes -o wide

cluster-down:
	kind delete cluster --name $(KIND_CLUSTER)

load:
	docker pull redis:7.4.2-alpine
	mkdir -p .cache
	docker image save --platform $(IMAGE_PLATFORM) --output .cache/kind-images.tar fab/tool-api:$(VERSION) fab/tool-simulator:$(VERSION) redis:7.4.2-alpine
	kind load image-archive .cache/kind-images.tar --name $(KIND_CLUSTER)

deploy:
	$(KUBECTL) apply -k deploy/k8s
	$(KUBECTL) -n fab rollout status deployment/redis --timeout=180s
	$(KUBECTL) -n fab rollout status deployment/tool-simulator --timeout=180s
	$(KUBECTL) -n fab rollout status deployment/tool-api --timeout=180s

status:
	$(KUBECTL) -n fab get pods -o wide
	$(KUBECTL) -n fab get services
	$(KUBECTL) -n fab get deployments

watch:
	$(KUBECTL) -n fab get pods -o wide -w

scale:
	$(KUBECTL) -n fab scale deployment/tool-api --replicas=$(N)
	$(KUBECTL) -n fab rollout status deployment/tool-api --timeout=180s

kill-pod:
	@pods=$$($(KUBECTL) -n fab get pods -l app=tool-api --field-selector=status.phase=Running -o name) || exit $$?; \
	pod=$$(printf '%s\n' "$$pods" | shuf -n 1); \
	if [ -z "$$pod" ]; then echo "No running tool-api pod found in kind-fab/fab"; exit 1; fi; \
	$(KUBECTL) -n fab delete "$$pod" --wait=false
