#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT_DIR/ios/ServerConsole/ServerConsole.xcodeproj"
DERIVED_DATA="$ROOT_DIR/ios/ServerConsole/DerivedData"

xcodebuild \
  -project "$PROJECT" \
  -scheme ServerConsole \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath "$DERIVED_DATA" \
  build
