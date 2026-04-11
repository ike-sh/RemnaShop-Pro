#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-remnashop}"
COMPOSE="docker compose -p ${PROJECT_NAME}"
VOLUME_NAME="${PROJECT_NAME}_remnashop-data"
BACKUP_DIR="${BACKUP_DIR:-./backup}"

print_help() {
  cat <<USAGE
RemnaShop-Pro Docker 一键运维脚本

用法:
  ./docker-manage.sh <command>

命令:
  init-env    初始化 .env（若不存在则由 .env.example 复制）
  up          一键构建并启动
  update      一键升级（git pull + 重建启动）
  restart     重启服务
  logs        查看实时日志
  ps          查看运行状态
  down        停止并移除容器（保留数据卷）
  backup      备份数据卷到 ./backup
  restore     从 ./backup 恢复数据卷
  help        显示帮助

可选环境变量:
  PROJECT_NAME   Compose 项目名（默认: remnashop）
  BACKUP_DIR     备份目录（默认: ./backup）
USAGE
}

ensure_env() {
  if [ ! -f .env ]; then
    if [ -f .env.example ]; then
      cp .env.example .env
      echo "[ok] 已创建 .env，请先编辑后再启动。"
    else
      echo "[err] .env.example 不存在，无法初始化 .env"
      exit 1
    fi
  fi
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "[err] 未检测到 docker，请先安装 Docker。"
    exit 1
  fi
}

cmd_init_env() {
  ensure_env
}

cmd_up() {
  require_docker
  ensure_env
  ${COMPOSE} up -d --build
  ${COMPOSE} ps
}

cmd_update() {
  require_docker
  ensure_env
  git pull --ff-only
  ${COMPOSE} up -d --build
  ${COMPOSE} ps
}

cmd_restart() {
  require_docker
  ${COMPOSE} restart remnashop
  ${COMPOSE} ps
}

cmd_logs() {
  require_docker
  ${COMPOSE} logs -f remnashop
}

cmd_ps() {
  require_docker
  ${COMPOSE} ps
}

cmd_down() {
  require_docker
  ${COMPOSE} down
}

cmd_backup() {
  require_docker
  mkdir -p "${BACKUP_DIR}"
  docker run --rm -v "${VOLUME_NAME}:/from" -v "$(pwd)/${BACKUP_DIR}:/to" alpine sh -c "cp -a /from/. /to/"
  echo "[ok] 备份完成: ${BACKUP_DIR}"
}

cmd_restore() {
  require_docker
  if [ ! -d "${BACKUP_DIR}" ]; then
    echo "[err] 备份目录不存在: ${BACKUP_DIR}"
    exit 1
  fi
  docker run --rm -v "${VOLUME_NAME}:/to" -v "$(pwd)/${BACKUP_DIR}:/from" alpine sh -c "cp -a /from/. /to/"
  echo "[ok] 恢复完成，建议执行: ./docker-manage.sh restart"
}

main() {
  local cmd="${1:-help}"
  case "${cmd}" in
    init-env) cmd_init_env ;;
    up) cmd_up ;;
    update) cmd_update ;;
    restart) cmd_restart ;;
    logs) cmd_logs ;;
    ps) cmd_ps ;;
    down) cmd_down ;;
    backup) cmd_backup ;;
    restore) cmd_restore ;;
    help|-h|--help) print_help ;;
    *)
      echo "[err] 未知命令: ${cmd}"
      print_help
      exit 1
      ;;
  esac
}

main "$@"
