# MTGLogger for iPhone

The iOS companion connects to the existing MTGLogger server. The Scan tab uses
AVFoundation still-photo capture and Vision rectangle detection. Collection,
Review, and Library use the live website in WKWebView, including decks, values,
database, play, editing, and exports through the iOS share sheet. There is one
server-side collection.

## Install with SideStore

Requires iOS 17 or later and an existing SideStore installation. Open the
website's **Get the iPhone app** button, download the IPA in Safari, then install
it with the + button in SideStore. Alternatively add the MTGLogger source URL in
SideStore Sources. SideStore handles signing with the user's own account; this
project does not collect Apple credentials or require a paid developer account.

The app defaults to `http://192.168.1.254:5173`. Connect from the home Wi-Fi, or
enter an existing trusted HTTPS address. The server has no built-in accounts;
use the existing protected access layer for remote access. This release does not
change DNS, router ports, or server exposure.

Official references: [SideStore app sources](https://docs.sidestore.io/docs/advanced/app-sources),
[Apple photo capture](https://developer.apple.com/documentation/avfoundation/avcapturephotooutput),
[Apple rectangle detection](https://developer.apple.com/documentation/vision/vndetectrectanglesrequest).

## Scanning behavior

- Capture automatically after a steady rectangle and settled focus/exposure, or
  tap the shutter deliberately for each physical copy.
- Automatic capture rearms only after the card has been absent for at least
  0.9 seconds. A brief missed detection or camera shake does not rearm it.
- Still images retain their orientation and are limited to a 2560-pixel longest
  side, encoded at 94% JPEG quality. The app does not use the PC as a camera bridge.
- Every capture is saved in the iPhone Application Support directory before
  upload. It retains its UUID, original server, and original batch defaults.
- Retry sends the same bytes and UUID. The server atomically commits a receipt
  alongside inventory/deck or review changes. Replaying it returns the original
  result; reusing an ID with different bytes or defaults returns HTTP 409.
- An interrupted upload remains in the outbox until successful retry. Capture
  pauses at 20 pending images. Review items stay on the server with their images.
- The app verifies server support for capture IDs before sending a saved scan.
  Old servers must be updated before app scanning is enabled.

## Build and release

On a Mac with Xcode and XcodeGen:

```sh
swift ios/scripts/prepare-assets.swift
cd ios
xcodegen generate
xcodebuild test -project MTGLogger.xcodeproj -scheme MTGLogger \
  -destination 'platform=iOS Simulator,name=iPhone 16' CODE_SIGNING_ALLOWED=NO
```

The `iPhone app` GitHub Actions workflow runs simulator unit tests, builds an
ARM64 `.app` without developer signing, and packages `Payload/MTGLogger.app` as
an IPA. SideStore re-signs it on installation. A tag named `ios-v*` additionally
publishes the IPA, its SHA-256, and a generated `source.json` from the built
Info.plist. Before a new release, update version/build in `ios/project.yml` and
the download link in `frontend/public/iphone.html`.

Source generation intentionally omits notarization/marketplace fields, which
SideStore interprets differently. Preserve old versioned release assets for
rollback; the source points to the exact versioned IPA.

## Acceptance on a physical phone

Simulator tests and an ARM64 build do not establish camera quality or successful
SideStore installation. Check these on the user's phone:

1. Install and connect over home Wi-Fi with local-network permission granted.
2. Capture a normal card, foil, basic land, and two identical physical copies.
3. Verify there is one receipt per capture and no stationary-card duplicate.
4. Interrupt Wi-Fi during an upload, quit/reopen, retry, and verify the quantity.
5. Resolve a review, edit collection/deck details, and export through the share sheet.
6. Background the app and change tabs; the camera must stop outside Scan.

Physical-device acceptance is pending until these checks are performed.
