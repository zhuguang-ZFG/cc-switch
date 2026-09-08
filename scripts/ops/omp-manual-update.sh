#!/bin/bash
# omp 手动版本更新（绕过 autoupdate.ps1 的 2 天成熟期门槛, 用于用户显式要求更新到最新）
# 用法: 修改下面 VER/EXPECTED 两个变量后运行
#   VER       目标版本号
#   EXPECTED  从 https://github.com/can1357/oh-my-pi/releases/download/v<VER>/SHA256SUMS.txt 获取
#   CUR       当前版本(备份命名用)
# 逻辑与 ~/.omp/omp-autoupdate/omp-autoupdate.ps1 一致:
#   下载(断点续传x2) -> SHA 校验 -> 备份 rename-aside -> 换入 -> 版本复核(不符回滚) -> 备份轮转(留3)
# 注意: 用 Git 的 bash 跑(工具 shell 的 `bash` 解析到 WSL): C:/PROGRA~1/Git/usr/bin/bash.exe <script>
set -u
VER='18.1.14'
CUR='18.1.13'
EXPECTED='f5b46850f92ad2c2aa337b094fbf51742417ecdd808c088e24bb3d21b19e5177'

BIN=/c/Users/zhugu/.bun/bin
EXE="$BIN/omp.exe"
LOG=/c/Users/zhugu/.omp/omp-autoupdate/autoupdate.log
URL="https://github.com/can1357/oh-my-pi/releases/download/v${VER}/omp-windows-x64.exe"
NEW="${TMPDIR:-/tmp}/omp-${VER}.exe"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [manual] $1" >> "$LOG"; echo "$1"; }

for attempt in 1 2; do
  [ $attempt -eq 2 ] && rm -f "$NEW"
  log "download attempt $attempt ($VER)"
  curl -sS -L -C - --retry 3 --retry-delay 5 --max-time 3000 -o "$NEW" "$URL" || { log "download attempt $attempt failed"; continue; }
  ACTUAL=$(sha256sum "$NEW" | cut -d' ' -f1)
  if [ "$ACTUAL" = "$EXPECTED" ]; then log "sha OK"; break; fi
  log "sha mismatch attempt $attempt: $ACTUAL"
  rm -f "$NEW"
  [ $attempt -eq 2 ] && { log 'abort: download/sha failed'; exit 1; }
done

[ -f "$NEW" ] || { log 'abort: no downloaded file'; exit 1; }
cp -f "$EXE" "$BIN/omp.exe.pre-update-$CUR.bak" && log "backup ok"
mv -f "$EXE" "$BIN/omp.exe.running-$CUR.hold" && log "aside ok"
cp -f "$NEW" "$EXE" && log "swap ok"
V=$("$EXE" --version 2>/dev/null | head -1 | tr -d '\r' | sed 's/^omp\///')
if [ "$V" != "$VER" ]; then
  log "rollback: got '$V' expected '$VER'"
  mv -f "$EXE" "$BIN/omp.exe.failed-$VER.hold" 2>/dev/null
  cp -f "$BIN/omp.exe.pre-update-$CUR.bak" "$EXE"
  log "rolled back to $CUR"
  exit 1
fi
log "updated to $VER OK"
rm -f "$NEW"
ls -t "$BIN"/omp.exe.pre-update-*.bak "$BIN"/omp.exe.running-*.hold 2>/dev/null | tail -n +4 | while read -r f; do rm -f "$f"; log "pruned $(basename "$f")"; done
