#!/usr/bin/env bash
# Makes room on a GitHub-hosted Ubuntu runner for the analyzer image (about 6 GB, held twice while BuildKit
# loads it into Docker) by deleting toolchains these jobs never use. Does nothing anywhere else: on a
# self-hosted runner or a laptop it only prints a line.
set -euo pipefail

if [[ ${GITHUB_ACTIONS:-} != true || ${RUNNER_ENVIRONMENT:-} != github-hosted || ${RUNNER_OS:-} != Linux ]]; then
  echo "不是 GitHub 提供的 Linux runner：不刪任何東西"
  exit 0
fi

echo "清理前："
df -h /

unused=(
  /usr/share/dotnet
  /usr/local/lib/android
  /opt/ghc
  /usr/local/.ghcup
  /opt/hostedtoolcache/CodeQL
  /usr/local/share/boost
  /usr/share/swift
  /usr/local/share/powershell
  /usr/local/share/chromium
  /usr/lib/jvm
  /opt/microsoft
  /opt/google
  /opt/az
)
# Deleting the Android SDK alone takes about a minute; remove the directories in parallel.
for path in "${unused[@]}"; do
  if [[ -e $path ]]; then
    sudo rm -rf "$path" &
  fi
done
wait

echo "清理後："
df -h /
