#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# Load function definitions without executing the script's main entrypoint.
source <(sed '/^main "\$@"$/d' "${project_root}/bootstrap.sh")
need_sudo

case_dir="$(mktemp -d)"
case "${case_dir}" in /tmp/tmp.*) ;; *) exit 1 ;; esac
test -d "${case_dir}" && test ! -L "${case_dir}"
trap 'rm -rf -- "${case_dir}"' EXIT

mkdir -p "${case_dir}/unknown/remnashop-pro"
printf 'keep this file\n' >"${case_dir}/unknown/remnashop-pro/user-data"
INSTALL_DIR="${case_dir}/unknown/remnashop-pro"
if output="$(prepare_repo 2>&1)"; then exit 1; fi
[[ "${output}" == *"已存在且非空"* ]]
test -f "${INSTALL_DIR}/user-data"

git() {
  if [ "$1" = "-C" ]; then
    case "$3" in
      remote) printf '%s\n' "${MOCK_REMOTE:-${REPO_URL}}" ;;
      status) if [ -n "${MOCK_DIRTY:-}" ]; then printf '%s\n' "${MOCK_DIRTY}"; fi ;;
      fetch|checkout|pull) printf '%s\n' "$3" >>"${case_dir}/git-actions" ;;
    esac
  elif [ "$1" = "clone" ]; then
    printf 'clone\n' >>"${case_dir}/git-actions"
  fi
}

INSTALL_DIR="${case_dir}/new/remnashop-pro"
prepare_repo >/dev/null
mkdir -p "${case_dir}/empty/remnashop-pro"
INSTALL_DIR="${case_dir}/empty/remnashop-pro"
prepare_repo >/dev/null
test "$(grep -c '^clone$' "${case_dir}/git-actions")" -eq 2

mkdir -p "${case_dir}/correct/remnashop-pro/.git"
printf 'keep\n' >"${case_dir}/correct/remnashop-pro/user-data"
INSTALL_DIR="${case_dir}/correct/remnashop-pro"
prepare_repo >/dev/null
test -f "${INSTALL_DIR}/user-data"
grep -q '^pull$' "${case_dir}/git-actions"

MOCK_DIRTY=' M user-data'
if output="$(prepare_repo 2>&1)"; then exit 1; fi
[[ "${output}" == *"未提交修改"* ]]
unset MOCK_DIRTY

mkdir -p "${case_dir}/other/remnashop-pro/.git"
printf 'keep\n' >"${case_dir}/other/remnashop-pro/user-data"
INSTALL_DIR="${case_dir}/other/remnashop-pro"
MOCK_REMOTE='https://github.com/someone-else/project.git'
if output="$(prepare_repo 2>&1)"; then exit 1; fi
[[ "${output}" == *"origin"* ]]
test -f "${INSTALL_DIR}/user-data"
if output="$(uninstall_flow 2>&1)"; then exit 1; fi
[[ "${output}" == *"校验"* ]]
unset MOCK_REMOTE

mkdir -p "${case_dir}/links"
ln -s "${case_dir}/unknown/remnashop-pro" "${case_dir}/links/remnashop-pro"
INSTALL_DIR="${case_dir}/links/remnashop-pro"
if output="$(prepare_repo 2>&1)"; then exit 1; fi
[[ "${output}" == *"符号链接"* ]]
test -f "${case_dir}/unknown/remnashop-pro/user-data"

INSTALL_DIR="${case_dir}/unsafe-name"
if output="$(prepare_repo 2>&1)"; then exit 1; fi
[[ "${output}" == *"必须以 remnashop-pro 命名"* ]]
echo "bootstrap directory protection PASS"
