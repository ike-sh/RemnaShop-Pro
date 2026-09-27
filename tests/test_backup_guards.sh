#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source <(sed '/^main "\$@"$/d' "${project_root}/docker-manage.sh")

case_dir="$(mktemp -d)"
mkdir -p "${case_dir}/project"
trap 'rm -f -- "${case_dir}/docker-runs"; rmdir -- "${case_dir}/project/backup" "${case_dir}/project" "${case_dir}" 2>/dev/null || true' EXIT
cd "${case_dir}/project"
PROJECT_ROOT="$(pwd -P)"

docker() {
  case "$1 $2" in
    "volume inspect") [ "${MOCK_VOLUME_EXISTS:-0}" = 1 ] ;;
    "ps -q")
      if [ "${MOCK_RUNNING:-0}" = 1 ]; then printf 'test-container-id\n'; fi
      ;;
    "run --rm") printf 'run\n' >>"${case_dir}/docker-runs" ;;
    *) return 0 ;;
  esac
}

BACKUP_DIR=""
if (cmd_backup) >/dev/null 2>&1; then exit 1; fi

BACKUP_DIR="${case_dir}/outside"
MOCK_VOLUME_EXISTS=0
if (cmd_backup) >/dev/null 2>&1; then exit 1; fi

MOCK_VOLUME_EXISTS=1
MOCK_RUNNING=1
if (cmd_backup) >/dev/null 2>&1; then exit 1; fi

MOCK_RUNNING=0
BACKUP_DIR="${case_dir}/project/backup"
if (cmd_backup) >/dev/null 2>&1; then exit 1; fi

REMNASHOP_RESTORE_CONFIRM=""
if (cmd_restore) >/dev/null 2>&1; then exit 1; fi

test ! -e "${case_dir}/docker-runs"
echo "backup and restore guards PASS"
