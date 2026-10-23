# Live Mobilelty (Android)

Control this phone from your BridgeBot laptop browser. Open-source companion app for https://bridgebot-ai.onrender.com/mobile/

## Build

```bash
cd android/live-mobilelty
gradle wrapper --gradle-version 8.7   # once
./gradlew assembleRelease
# APK: app/build/outputs/apk/release/
```

CI (`.github/workflows/android-apk.yml`) builds a signed APK for free on every change to this folder and publishes it to the `live-mobilelty-v1` GitHub release. The site redirects `/downloads/live-mobilelty.apk` there.

## Permissions

- **MediaProjection** — screen JPEG frames over WebSocket
- **AccessibilityService** — taps, swipes, Back / Home / Recents, text
- **Foreground service + notification** — always visible while connected; Stop button

## Protocol

Same as the desktop connector: outbound `/ws/connect` with a `bbk_` key, `HELLO` with `features: ["android","remote_desktop"]`, then `RD_START` / `RD_FRAME` / `RD_INPUT` / `RD_STOP` relayed owner-only through `/ws/dashboard`.
