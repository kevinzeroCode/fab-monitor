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
V ?= v2
# 使用者的 kind 節點為 Linux AMD64；匯出單一平台避免缺少其他平台的 digest。
IMAGE_PLATFORM ?= linux/amd64
# 本專案已使用 Docker，明確指定 provider 避免誤選其他容器引擎。
export KIND_EXPERIMENTAL_PROVIDER = docker
# D7 固定 chart 版本；所有 Helm 動作也明確指定 kind-fab。
MONITORING_CHART_VERSION := 92.14.1
RPS ?= 20

.PHONY: help build up down logs test ps cluster cluster-down load load-images deploy deploy-observability status watch scale kill-pod rollout rollback break-redis fix-redis monitoring grafana prometheus chaos-latency chaos-errors chaos-clear
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
	@echo "Phase 2 - Kubernetes (D5: rollout and probes)"
	@echo "  make rollout [V=v2]      Build/load then update API image and version together"
	@echo "  make rollback            Restore previous API Pod template and wait"
	@echo "  make break-redis         Set Redis desired replicas to zero"
	@echo "  make fix-redis           Restore Redis and wait for API availability"
	@echo "Phase 3a - D7 observability"
	@echo "  make load-images VERSION=v3  Load built D7 images without running loadgen"
	@echo "  make deploy-observability Apply D7 v3 overlay; keep base v1 lab intact"
	@echo "  make monitoring          Install pinned monitoring stack and app monitors"
	@echo "  make grafana             Show local credentials; forward localhost:3000"
	@echo "  make prometheus          Forward Prometheus to localhost:9090"
	@echo "  make load S=60 [RPS=20]  Send fixed-rate requests (needs dev dependencies)"
	@echo "Phase 3b - D8 alert drills"
	@echo "  make chaos-latency       Add 500ms latency on every API pod"
	@echo "  make chaos-errors        Fail 20% of API requests on every API pod"
	@echo "  make chaos-clear         Clear injected faults on every API pod"

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
ifneq ($(strip $(S)),)
	$(PYTHON) tools/loadgen.py --seconds $(S) --rps $(RPS)
else
	$(MAKE) load-images
endif

# 保留舊版 make load 的映像匯入行為；明確指定 S 才切換成 Phase 3 壓測。
# load-images 提供無歧義名稱，之後也可直接呼叫，不影響既有 rollout.py。
load-images:
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

rollout:
	$(PYTHON) tools/rollout.py "$(V)" --context "$(KUBE_CONTEXT)"

rollback:
	$(KUBECTL) -n fab rollout undo deployment/tool-api
	$(KUBECTL) -n fab rollout status deployment/tool-api --timeout=180s

break-redis:
	$(KUBECTL) -n fab scale deployment/redis --replicas=0

fix-redis:
	$(KUBECTL) -n fab scale deployment/redis --replicas=1
	$(KUBECTL) -n fab rollout status deployment/redis --timeout=180s
	$(KUBECTL) -n fab rollout status deployment/tool-api --timeout=180s

monitoring:
	helm upgrade --install fab-monitor kube-prometheus-stack --repo https://prometheus-community.github.io/helm-charts --version $(MONITORING_CHART_VERSION) --kube-context $(KUBE_CONTEXT) --namespace monitoring --create-namespace --values deploy/monitoring/values.yaml --wait --timeout 10m
	$(KUBECTL) wait --for=condition=Established crd/servicemonitors.monitoring.coreos.com --timeout=180s
	$(KUBECTL) apply -k deploy/monitoring
	$(KUBECTL) -n monitoring wait --for=create statefulset/prometheus-fab-monitor-prometheus --timeout=180s
	$(KUBECTL) -n monitoring rollout status statefulset/prometheus-fab-monitor-prometheus --timeout=300s

deploy-observability:
	$(KUBECTL) apply -k deploy/overlays/observability
	$(KUBECTL) -n fab rollout status deployment/redis --timeout=180s
	$(KUBECTL) -n fab rollout status deployment/tool-simulator --timeout=180s
	$(KUBECTL) -n fab rollout status deployment/tool-api --timeout=180s

grafana:
	@echo "Grafana: http://localhost:3000 (user: admin)"
	@$(KUBECTL) -n monitoring get secret fab-grafana -o jsonpath='{.data.admin-password}' | base64 --decode; echo
	$(KUBECTL) -n monitoring port-forward --address 127.0.0.1 service/fab-grafana 3000:80

prometheus:
	@echo "Prometheus: http://localhost:9090 (local lab; no login)"
	$(KUBECTL) -n monitoring port-forward --address 127.0.0.1 service/fab-monitor-prometheus 9090:9090

# chaos 存在每個 Pod 的記憶體；由 tools/chaos.py 逐一進 Pod 設定，不能只打 NodePort。
chaos-latency chaos-errors chaos-clear:
	$(PYTHON) tools/chaos.py $(patsubst chaos-%,%,$@) --context "$(KUBE_CONTEXT)"
