#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR/.."

if command -v gitleaks >/dev/null 2>&1; then
  exec gitleaks detect --no-git --redact --config .gitleaks.toml --source .
fi

# Deterministic fallback for provider tokens and private-key material.
if rg -n --hidden \
  --glob '!node_modules/**' \
  --glob '!.venv/**' \
  --glob '!.git/**' \
  --glob '!.env.example' \
  --glob '!.omo/**' \
  '(sk-(proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)' .
then
  echo "Potential secret material detected." >&2
  exit 1
fi

echo "No provider-token or private-key patterns detected."
