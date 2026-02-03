#!/bin/bash
# update-dns.sh — EC2起動時にCloudflare DNSのAレコードを現在のパブリックIPで更新する
set -euo pipefail

# 設定ファイルから読み込み
CONFIG_FILE="/opt/axonrelay/dns-config.env"
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: $CONFIG_FILE not found"
  exit 1
fi
source "$CONFIG_FILE"

# 必須変数チェック
for var in CF_API_TOKEN CF_ZONE_ID CF_RECORD_NAME; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: $var is not set in $CONFIG_FILE"
    exit 1
  fi
done

# EC2メタデータからパブリックIPを取得 (IMDSv2)
TOKEN=$(curl -sf -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
PUBLIC_IP=$(curl -sf -H "X-aws-ec2-metadata-token: $TOKEN" \
  "http://169.254.169.254/latest/meta-data/public-ipv4")

if [[ -z "$PUBLIC_IP" ]]; then
  echo "ERROR: Failed to get public IP from EC2 metadata"
  exit 1
fi
echo "Current public IP: $PUBLIC_IP"

# 既存のAレコードIDを取得
RECORD_RESPONSE=$(curl -sf "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?type=A&name=${CF_RECORD_NAME}" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json")

RECORD_ID=$(echo "$RECORD_RESPONSE" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['result'][0]['id'] if r['result'] else '')" 2>/dev/null || true)
CURRENT_IP=$(echo "$RECORD_RESPONSE" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['result'][0]['content'] if r['result'] else '')" 2>/dev/null || true)

if [[ "$CURRENT_IP" == "$PUBLIC_IP" ]]; then
  echo "DNS already points to $PUBLIC_IP — no update needed"
  exit 0
fi

if [[ -n "$RECORD_ID" ]]; then
  # 既存レコードを更新
  curl -sf -X PUT "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/${RECORD_ID}" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "{\"type\":\"A\",\"name\":\"${CF_RECORD_NAME}\",\"content\":\"${PUBLIC_IP}\",\"ttl\":300,\"proxied\":false}" \
    | python3 -c "import sys,json; r=json.load(sys.stdin); print('OK: updated' if r['success'] else f'FAIL: {r}')"
else
  # レコードが無ければ新規作成
  curl -sf -X POST "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "{\"type\":\"A\",\"name\":\"${CF_RECORD_NAME}\",\"content\":\"${PUBLIC_IP}\",\"ttl\":300,\"proxied\":false}" \
    | python3 -c "import sys,json; r=json.load(sys.stdin); print('OK: created' if r['success'] else f'FAIL: {r}')"
fi

echo "DNS updated: ${CF_RECORD_NAME} -> ${PUBLIC_IP}"
