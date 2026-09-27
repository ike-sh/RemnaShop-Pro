#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source <(sed '/^main "\$@"$/d' "${project_root}/bootstrap.sh")
need_sudo

case_dir="$(mktemp -d)"
case "${case_dir}" in /tmp/tmp.*) ;; *) exit 1 ;; esac
test -d "${case_dir}" && test ! -L "${case_dir}"
trap 'rm -rf -- "${case_dir}"' EXIT

INSTALL_DIR="${case_dir}/RemnaShop-Pro"
mkdir -p "${INSTALL_DIR}/.git"
touch "${INSTALL_DIR}/bootstrap.sh" "${INSTALL_DIR}/docker-compose.yml"
actions="${case_dir}/actions"

(
  git() {
    if [ "$1" = "-C" ] && [ "$3" = "remote" ]; then printf '%s\n' "${REPO_URL}"; fi
  }
  docker() {
    printf '%s\n' "$*" >>"${actions}"
    case "$1 $2 $3" in
      "ps -aq --filter") printf 'rc-container-id\n' ;;
      "volume ls -q") printf 'rc-volume-id\n' ;;
      "image ls -q") printf 'rc-image-id\n' ;;
    esac
  }
  rm() {
    printf 'filesystem-rm %s\n' "$*" >>"${actions}"
  }
  confirm_uninstall_if_needed() { :; }
  uninstall_flow >/dev/null
)

grep -q -- "-p remnashop down -v --rmi local --remove-orphans" "${actions}"
grep -q -- "rm -f rc-container-id" "${actions}"
grep -q -- "volume rm rc-volume-id" "${actions}"
grep -q -- "image rm rc-image-id" "${actions}"
grep -q -- "filesystem-rm -rf -- ${INSTALL_DIR}" "${actions}"
if grep -q 'unrelated' "${actions}"; then exit 1; fi
test -f "${INSTALL_DIR}/bootstrap.sh"
echo "uninstall project scope PASS"
