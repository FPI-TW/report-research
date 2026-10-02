#!/usr/bin/env bash
# 把 deploy/systemd/ 的 unit 安裝到 /etc/systemd/system，並把辦公室主機的帳號與路徑換成目標主機的。
#
# repo 裡的 unit 維持辦公室主機的字面值（tests/test_deploy_units.py 守的就是它們）；這支只在
# 安裝那一刻代換三樣東西，順序固定（先長後短，免得 repo 路徑被 HOME 代換吃掉一半）：
#   /home/kashionz/projects/report-mark → --root
#   /home/kashionz                      → --user 的 HOME
#   User=kashionz／Group=kashionz       → --user
# systemd 的 User=、WorkingDirectory= 不展開環境變數，所以只能在安裝時代換，不能靠 EnvironmentFile。
#
# 用法（在目標主機的 repo 根）：
#   sudo deploy/install_units.sh --user ubuntu --root /home/ubuntu/report-mark \
#     report-mark-web.service report-mark-sync.timer report-mark-health.timer
# 給 .timer 會連同同名 .service 一起裝；每支 unit 都 OnFailure= 告警模板，所以
# report-mark-alert@.service 一律裝。report-mark-web.service 會連同 .service.d/ drop-in 一起裝。
# 只複製與 daemon-reload，不 enable、不 start——開哪些排程是人的決定。
set -euo pipefail

SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/systemd" && pwd)
DEST_DIR=${SYSTEMD_DEST_DIR:-/etc/systemd/system}
PROD_ROOT=/home/kashionz/projects/report-mark
PROD_HOME=/home/kashionz
PROD_USER=kashionz

user="" root=""
units=()
while [ $# -gt 0 ]; do
  case "$1" in
    --user) user="$2"; shift 2 ;;
    --root) root="$2"; shift 2 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    -*) echo "不認得的選項：$1" >&2; exit 2 ;;
    *) units+=("$1"); shift ;;
  esac
done
if [ -z "$user" ] || [ -z "$root" ] || [ ${#units[@]} -eq 0 ]; then
  echo "用法：$0 --user USER --root REPO_ROOT UNIT..." >&2; exit 2
fi
home=$(getent passwd "$user" | cut -d: -f6)
if [ -z "$home" ]; then echo "找不到使用者 $user" >&2; exit 2; fi

render() {  # $1 來源檔 → stdout
  sed -e "s#${PROD_ROOT}#${root}#g" \
      -e "s#${PROD_HOME}#${home}#g" \
      -e "s#^User=${PROD_USER}\$#User=${user}#" \
      -e "s#^Group=${PROD_USER}\$#Group=${user}#" "$1"
}

install_one() {  # $1 unit 檔名
  local src="$SRC_DIR/$1"
  if [ ! -f "$src" ]; then echo "deploy/systemd/ 沒有 $1" >&2; exit 2; fi
  render "$src" | install -m 0644 /dev/stdin "$DEST_DIR/$1"
  echo "installed $DEST_DIR/$1"
  if [ -d "$src.d" ]; then
    install -d -m 0755 "$DEST_DIR/$1.d"
    for conf in "$src.d"/*.conf; do
      render "$conf" | install -m 0644 /dev/stdin "$DEST_DIR/$1.d/$(basename "$conf")"
      echo "installed $DEST_DIR/$1.d/$(basename "$conf")"
    done
  fi
}

declare -A done_units=()
queue=(report-mark-alert@.service)
for u in "${units[@]}"; do
  queue+=("$u")
  case "$u" in *.timer) queue+=("${u%.timer}.service") ;; esac
done
for u in "${queue[@]}"; do
  [ -n "${done_units[$u]:-}" ] && continue
  install_one "$u"
  done_units[$u]=1
done

# 代換後若還留著辦公室主機的字面值，代表來源檔多了一種寫法這支沒涵蓋——寧可停下也不要裝上半套。
if [ "$home" != "$PROD_HOME" ] \
  && grep -rl -e "$PROD_HOME" -e "^User=$PROD_USER\$" "${queue[@]/#/$DEST_DIR/}" "$DEST_DIR"/report-mark-web.service.d 2>/dev/null; then
  echo "上列檔案仍含 $PROD_HOME 或 User=$PROD_USER：請補 render() 的代換規則" >&2; exit 1
fi

if [ "$DEST_DIR" = /etc/systemd/system ]; then systemctl daemon-reload; fi
echo "done（未 enable／start）"
