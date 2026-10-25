#!/usr/bin/env bash
# Build an unsigned .ipa for a real iPhone out of the Briefcase project.
#
# Briefcase itself only builds for the iOS *simulator* and deliberately refuses
# to make a distributable IPA ("iOS apps must be published through Xcode"), so
# this script does the last step by hand:
#
#   1. briefcase create iOS      -> build/checkersbot_ios/ios/xcode/CheckersBot.xcodeproj
#                                   (0.4.x layout; older Briefcase used iOS/Xcode/<App>)
#   2. briefcase build iOS       -> simulator build (proves the app compiles and
#                                   installs the Python requirements)
#   3. xcodebuild -sdk iphoneos  -> the same project for a real device, with code
#                                   signing switched off
#   4. Payload/ + ditto          -> dist/CheckersBot-unsigned.ipa
#
# A sideload tool (iLoader / Sideloadly / AltStore) re-signs that IPA with your
# own certificate and installs it - no Apple developer account needed to build.
#
#   macOS:  bash scripts/build_ipa.sh
#   CI:     .github/workflows/ios.yml runs exactly this script
#   Windows: nothing here runs; push the repo and let CI do it
#
# Environment overrides:
#   FORMAL_NAME    app/target name (default: CheckersBot)
#   OUT            output folder (default: dist)
#   CONFIGURATION  Xcode configuration (default: Release)
#   SDK            Xcode SDK (default: iphoneos)

set -euo pipefail

FORMAL_NAME="${FORMAL_NAME:-CheckersBot}"
OUT="${OUT:-dist}"
CONFIGURATION="${CONFIGURATION:-Release}"
SDK="${SDK:-iphoneos}"

say() { printf '\n==> %s\n' "$*"; }

say "Tools"
briefcase --version
xcodebuild -version

say "Creating the Xcode project"
briefcase create iOS --no-input

say "Installing requirements + simulator build"
briefcase build iOS --no-input

say "Locating the Xcode project"
# Briefcase's layout moved to build/<app>/ios/xcode in 0.4.x, older versions used
# iOS/Xcode/<App> - just search for the project instead of assuming a path.
PROJECT="$(find . -maxdepth 8 -type d -name '*.xcodeproj' -not -path './.git/*' | head -n 1)"
if [ -z "$PROJECT" ]; then
  echo "! no .xcodeproj found - briefcase create iOS did not finish" >&2
  find . -maxdepth 4 -type d -not -path './.git*' | sort | head -n 60 >&2
  exit 1
fi
echo "project: $PROJECT"

# Prefer the target named after the app; otherwise take the project's first one.
TARGET="$FORMAL_NAME"
DETECTED="$(xcodebuild -list -json -project "$PROJECT" 2>/dev/null \
  | python3 -c 'import json,sys
try:
    targets = json.load(sys.stdin)["project"]["targets"]
except Exception:
    targets = []
print("\n".join(targets))' 2>/dev/null || true)"
if [ -n "$DETECTED" ]; then
  if ! printf '%s\n' "$DETECTED" | grep -qx "$TARGET"; then
    TARGET="$(printf '%s\n' "$DETECTED" | head -n 1)"
  fi
fi
echo "target: $TARGET"

say "Building for the device ($SDK, $CONFIGURATION, unsigned)"
rm -rf build-ios
BUILD_LOG="$PWD/xcodebuild-ios.log"
# CODE_SIGNING_ALLOWED=NO keeps the app itself unsigned, but the support
# package's "Process Python libraries" phase still runs
#   codesign --sign "$EXPANDED_CODE_SIGN_IDENTITY"
# on every module it turns into a framework.  With an empty identity that dies
# with "no identity found", so we hand it the ad-hoc identity "-": no
# certificate is involved, and iLoader re-signs everything when installing.
if ! xcodebuild build \
  -project "$PROJECT" \
  -target "$TARGET" \
  -configuration "$CONFIGURATION" \
  -sdk "$SDK" \
  SYMROOT="$PWD/build-ios" \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_IDENTITY="" \
  EXPANDED_CODE_SIGN_IDENTITY="-" \
  > "$BUILD_LOG" 2>&1; then
  echo "! the device build failed - last 150 lines of $BUILD_LOG:" >&2
  tail -n 150 "$BUILD_LOG" >&2 || true
  exit 1
fi

APP="$(find build-ios -type d -name '*.app' -path "*${CONFIGURATION}-${SDK}*" | head -n 1)"
if [ -z "$APP" ]; then
  echo "! the device build produced no .app - here is what was built:" >&2
  find build-ios -maxdepth 4 >&2 || true
  exit 1
fi
echo "app: $APP"

say "Wrapping the .app into an IPA"
rm -rf build-ipa
mkdir -p build-ipa/Payload
cp -R "$APP" build-ipa/Payload/
mkdir -p "$OUT"
IPA="$OUT/${FORMAL_NAME}-unsigned.ipa"
rm -f "$IPA"
(cd build-ipa && ditto -c -k --sequesterRsrc --keepParent Payload "../$IPA")

echo
echo "OK: $IPA  ($(du -h "$IPA" | cut -f1))"
echo "Next: sign it with iLoader (or Sideloadly / AltStore) and install it on the phone."
