#!/bin/bash
# Allocate the next public YYYY.W.R into VERSION and release-counter.json.
# Does not tag, push, or build. A CI build does not run this.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
go run ./cmd/allocate-version
