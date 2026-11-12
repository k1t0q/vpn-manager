#!/usr/bin/env bash
set -euo pipefail
umask 077
test -n "${DEPLOY_KEY:-}"
test -n "${KNOWN_HOSTS:-}"
task_kind="${DEPLOY_ACTION:-deploy}"
commit_sha="${RELEASE_SHA:?Missing release SHA}"
[[ "$task_kind" == deploy || "$task_kind" == rollback ]]
[[ "$commit_sha" =~ ^[0-9a-f]{40}$ ]]
key_path="$RUNNER_TEMP/vpn-stage-key"
hosts_path="$RUNNER_TEMP/vpn-stage-hosts"
trap 'rm -f "$key_path" "$hosts_path"' EXIT
printf '%s\n' "$DEPLOY_KEY" > "$key_path"
printf '%s\n' "$KNOWN_HOSTS" > "$hosts_path"
ssh -i "$key_path" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$hosts_path" \
  -o ConnectTimeout=15 vpn-deploy@185.23.239.142 "$task_kind $commit_sha"
