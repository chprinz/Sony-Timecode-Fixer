#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$ROOT_DIR/NativeApp/Info.plist")
DMG_PATH="$ROOT_DIR/Sony Timecode Fixer v$VERSION.dmg"

"$ROOT_DIR/Scripts/build-app.sh"
hdiutil create \
  -volname "Sony Timecode Fixer" \
  -srcfolder "$ROOT_DIR/Sony Timecode Fixer.app" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Built: $DMG_PATH"
