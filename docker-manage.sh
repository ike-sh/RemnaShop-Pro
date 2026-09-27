#!/usr/bin/env bash
set -euo pipefail
umask 077

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_NAME="remnashop"
COMPOSE="docker compose -p ${PROJECT_NAME}"
VOLUME_NAME="${PROJECT_NAME}_remnashop-data"
IMAGE_NAME="${PROJECT_NAME}-remnashop:latest"
BACKUP_DIR="${BACKUP_DIR:-}"

print_help() {
  cat <<USAGE
RemnaShop-Pro Docker 一键运维脚本

用法:
  ./docker-manage.sh <command>

命令:
  init-env    初始化 .env（若不存在则由 .env.example 复制）
  up          通过 bootstrap 安装/启动并等待健康
  update      通过 bootstrap 安全更新并等待健康
  restart     重启服务
  logs        查看实时日志
  ps          查看运行状态
  down        停止并移除容器（保留数据卷）
  backup      服务停止后备份数据卷到仓库外空目录并校验
  restore     服务停止后从已校验备份恢复（需 REMNASHOP_RESTORE_CONFIRM=YES）
  help        显示帮助

可选环境变量:
  BACKUP_DIR     必填：仓库外的备份目录；再次备份请选择新空目录
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
  bash ./bootstrap.sh install
}

cmd_update() {
  bash ./bootstrap.sh install
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

require_stopped_service() {
  local running
  running="$(docker ps -q --filter "volume=${VOLUME_NAME}")"
  if [ -n "${running}" ]; then
    echo "[err] 项目数据卷仍被运行中的容器使用。请先执行 docker compose -p remnashop stop remnashop。"
    exit 1
  fi
}

require_data_volume() {
  if ! docker volume inspect "${VOLUME_NAME}" >/dev/null 2>&1; then
    echo "[err] 数据卷不存在：${VOLUME_NAME}；不会创建空数据卷。"
    exit 1
  fi
}

require_backup_path() {
  if [ -z "${BACKUP_DIR}" ]; then
    echo "[err] 请指定仓库外的 BACKUP_DIR，避免卸载时删除备份。"
    exit 1
  fi
}

check_backup_outside_project() {
  local backup_path="$1"
  case "${backup_path}" in
    "${PROJECT_ROOT}"|"${PROJECT_ROOT}"/*)
      echo "[err] 备份目录不能位于项目仓库内。"
      exit 1
      ;;
  esac
}

verify_backup_dir() {
  local path="$1"
  local verifier
  verifier="${PROJECT_ROOT}/docker/verify_backup.py"
  docker run --rm -v "${path}:/snapshot" -v "${verifier}:/verify_backup.py:ro" --entrypoint python "${IMAGE_NAME}" /verify_backup.py /snapshot
}

cmd_backup() {
  require_docker
  require_backup_path
  require_data_volume
  require_stopped_service
  if [ -d "${BACKUP_DIR}" ] && [ -n "$(ls -A "${BACKUP_DIR}")" ]; then
    echo "[err] 备份目录非空，请选择新的空目录，避免覆盖旧备份。"
    exit 1
  fi
  mkdir -p -m 700 "${BACKUP_DIR}"
  local backup_path
  backup_path="$(cd "${BACKUP_DIR}" && pwd -P)"
  check_backup_outside_project "${backup_path}"
  docker run --rm -v "${VOLUME_NAME}:/from:ro" -v "${backup_path}:/to" --entrypoint sh "${IMAGE_NAME}" -c 'test -f /from/config.json && test -f /from/starlight.db && cp -a /from/. /to/'
  verify_backup_dir "${backup_path}"
  echo "[ok] 备份完成并校验通过: ${backup_path}"
}

cmd_restore() {
  require_docker
  require_backup_path
  require_data_volume
  require_stopped_service
  if [ "${REMNASHOP_RESTORE_CONFIRM:-}" != "YES" ]; then
    echo "[err] 恢复将覆盖项目数据；确认后设置 REMNASHOP_RESTORE_CONFIRM=YES。"
    exit 1
  fi
  if [ ! -d "${BACKUP_DIR}" ]; then
    echo "[err] 备份目录不存在: ${BACKUP_DIR}"
    exit 1
  fi
  local backup_path
  backup_path="$(cd "${BACKUP_DIR}" && pwd -P)"
  check_backup_outside_project "${backup_path}"
  verify_backup_dir "${backup_path}"
  docker run --rm -v "${VOLUME_NAME}:/to" -v "${backup_path}:/from:ro" --entrypoint sh "${IMAGE_NAME}" -c 'rm -f /to/starlight.db-wal /to/starlight.db-shm && cp -a /from/. /to/'
  docker run --rm -v "${VOLUME_NAME}:/snapshot" -v "${PROJECT_ROOT}/docker/verify_backup.py:/verify_backup.py:ro" --entrypoint python "${IMAGE_NAME}" /verify_backup.py /snapshot
  echo "[ok] 恢复完成并校验通过；请按安装流程启动项目。"
}

main() {
  local cmd="${1:-help}"
  cd "${PROJECT_ROOT}"
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
