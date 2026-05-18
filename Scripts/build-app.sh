#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
APP_NAME="Sony Timecode Fixer.app"
APP_PATH="$ROOT_DIR/$APP_NAME"
EXECUTABLE_PATH="$APP_PATH/Contents/MacOS/Sony Timecode Fixer"
ICON_PATH="$ROOT_DIR/Build/SonyTimecodeFixer.icns"

mkdir -p "$ROOT_DIR/Build"
swift "$ROOT_DIR/Scripts/make-icon.swift" "$ROOT_DIR"

rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources"

cp "$ROOT_DIR/NativeApp/Info.plist" "$APP_PATH/Contents/Info.plist"
cp "$ROOT_DIR/fcpxml_tc_patcher.py" "$APP_PATH/Contents/Resources/fcpxml_tc_patcher.py"
cp "$ICON_PATH" "$APP_PATH/Contents/Resources/SonyTimecodeFixer.icns"

swiftc \
  "$ROOT_DIR/NativeApp/Sources/SonyTimecodeFixer/SonyTimecodeFixerApp.swift" \
  "$ROOT_DIR/NativeApp/Sources/SonyTimecodeFixer/ContentView.swift" \
  "$ROOT_DIR/NativeApp/Sources/SonyTimecodeFixer/PatcherRunner.swift" \
  -o "$EXECUTABLE_PATH"

echo "Built: $APP_PATH"
