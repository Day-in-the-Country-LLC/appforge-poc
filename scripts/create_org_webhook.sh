#!/usr/bin/env bash
set -euo pipefail

error() {
  echo "❌ ERROR: $*" >&2
  exit 1
}

org=""
url=""
secret=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --org)
      org="${2:-}"
      shift 2
      ;;
    --url)
      url="${2:-}"
      shift 2
      ;;
    --secret)
      secret="${2:-}"
      shift 2
      ;;
    --help)
      cat <<'USAGE'
Usage:
  scripts/create_org_webhook.sh --org <org> --url <listener-url> --secret <secret>
USAGE
      exit 0
      ;;
    *)
      error "Unknown argument: $1"
      ;;
  esac
done

if [[ -z "$org" ]]; then
  error "Missing --org"
fi

if [[ -z "$url" ]]; then
  error "Missing --url"
fi

if [[ -z "$secret" ]]; then
  error "Missing --secret"
fi

if ! command -v gh >/dev/null 2>&1; then
  error "GitHub CLI 'gh' not found on PATH"
fi

if ! gh auth status -h github.com >/dev/null 2>&1; then
  error "GitHub CLI is not authenticated. Run 'gh auth login'."
fi

events='["projects_v2_item","issue_comment"]'

response=""
if ! response=$(gh api \
  -X POST "orgs/${org}/hooks" \
  -f name=web \
  -f active=true \
  -f events="${events}" \
  -f config[content_type]=json \
  -f config[url]="${url}" \
  -f config[secret]="${secret}" 2>&1); then
  error "gh api failed: ${response}"
fi

echo "${response}"
