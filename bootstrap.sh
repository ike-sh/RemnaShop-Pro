#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ike666888/RemnaShop-Pro.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/remnashop-pro}"
BRANCH="${BRANCH:-main}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() {
  echo -e "${GREEN}[remnashop-bootstrap]${NC} $*"
}

warn() {
  echo -e "${YELLOW}[warn]${NC} $*"
}

err() {
  echo -e "${RED}[error]${NC} $*" >&2
}

need_sudo() {
  if [ "${EUID}" -eq 0 ]; then
    SUDO=""
  else
    SUDO="sudo"
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_docker_if_missing() {
  if require_cmd docker; then
    log "Docker already installed: $(docker --version)"
    return
  fi

  log "Docker not found, installing Docker..."
  curl -fsSL https://get.docker.com | sh
  ${SUDO} systemctl enable docker >/dev/null 2>&1 || true
  ${SUDO} systemctl start docker >/dev/null 2>&1 || true

  if ! require_cmd docker; then
    err "Docker installation failed."
    exit 1
  fi

  log "Docker installed: $(docker --version)"
}

install_compose_if_missing() {
  if docker compose version >/dev/null 2>&1; then
    log "Docker Compose already available: $(docker compose version | head -n 1)"
    return
  fi

  log "Docker Compose plugin not found, installing..."

  if require_cmd apt-get; then
    ${SUDO} apt-get update -y
    ${SUDO} apt-get install -y docker-compose-plugin
  elif require_cmd dnf; then
    ${SUDO} dnf install -y docker-compose-plugin
  elif require_cmd yum; then
    ${SUDO} yum install -y docker-compose-plugin
  else
    err "Unsupported package manager. Please install Docker Compose plugin manually."
    exit 1
  fi

  if ! docker compose version >/dev/null 2>&1; then
    err "Docker Compose installation failed."
    exit 1
  fi

  log "Docker Compose installed: $(docker compose version | head -n 1)"
}

clone_or_update_repo() {
  log "Preparing repository at ${INSTALL_DIR}"

  if [ -d "${INSTALL_DIR}/.git" ]; then
    log "Existing git repository detected, updating..."
    git -C "${INSTALL_DIR}" fetch origin
    git -C "${INSTALL_DIR}" checkout "${BRANCH}"
    git -C "${INSTALL_DIR}" pull --ff-only origin "${BRANCH}"
  else
    ${SUDO} mkdir -p "$(dirname "${INSTALL_DIR}")"
    if [ -d "${INSTALL_DIR}" ]; then
      warn "${INSTALL_DIR} exists but is not a git repository. Removing it to avoid conflicts."
      ${SUDO} rm -rf "${INSTALL_DIR}"
    fi
    git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
  fi
}

prepare_env() {
  cd "${INSTALL_DIR}"

  if [ ! -f .env ] && [ -f .env.example ]; then
    cp .env.example .env
    log "Created .env from .env.example"
    warn "Please edit ${INSTALL_DIR}/.env and set at least ADMIN_ID and BOT_TOKEN"
  elif [ -f .env ]; then
    log ".env already exists, keeping current values"
  else
    err "Missing .env.example, cannot prepare environment file."
    exit 1
  fi
}

start_stack() {
  cd "${INSTALL_DIR}"
  log "Starting Docker Compose stack..."
  docker compose up -d --build
}

verify_stack() {
  cd "${INSTALL_DIR}"

  log "Verification: Docker"
  docker --version

  log "Verification: Docker Compose"
  docker compose version

  log "Verification: environment file"
  if [ -f .env ]; then
    echo "[ok] ${INSTALL_DIR}/.env exists"
  else
    err ".env file does not exist"
    exit 1
  fi

  log "Verification: stack status"
  docker compose ps

  log "Verification: service health"
  container_id="$(docker compose ps -q remnashop || true)"
  if [ -n "${container_id}" ]; then
    health="$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "${container_id}")"
    echo "[ok] remnashop container health: ${health}"
  else
    err "remnashop container not found"
    exit 1
  fi
}

print_success() {
  cat <<MSG

✅ RemnaShop-Pro bootstrap completed.

Install directory: ${INSTALL_DIR}
Next steps:
  1) Edit ${INSTALL_DIR}/.env if needed
  2) Check status: cd ${INSTALL_DIR} && docker compose ps
  3) Check logs:   cd ${INSTALL_DIR} && docker compose logs -f remnashop

MSG
}

main() {
  need_sudo

  if ! require_cmd curl; then
    err "curl is required but not found."
    exit 1
  fi
  if ! require_cmd git; then
    err "git is required but not found. Please install git first."
    exit 1
  fi

  install_docker_if_missing
  install_compose_if_missing
  clone_or_update_repo
  prepare_env
  start_stack
  verify_stack
  print_success
}

main "$@"
