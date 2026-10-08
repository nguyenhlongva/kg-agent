#!/bin/sh
# Sao lưu database mỗi ngày (thêm vào crontab: 0 2 * * * /opt/kb-agent/deploy/backup.sh)
set -e
cd "$(dirname "$0")/.."
mkdir -p backups
sqlite3 data/kb.sqlite3 ".backup 'backups/kb-$(date +%F).sqlite3'"
find backups -name 'kb-*.sqlite3' -mtime +30 -delete
