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

- The Auto lens can switch to an autofocus ultrawide on supported phones. The
  lens menu also offers Main and Close-up. Fixed-focus ultrawides are not offered
  as close-up cameras. Other phones retain the main rear camera.
- Suggested zoom accounts for the camera's minimum focus distance, field of
  view, and a 63 mm card. Step back and use the zoom slider to fill the guide;
  the reset button restores the suggestion. Tap the card in the preview to set
  focus and exposure there. A yellow ring confirms the tap.
- Capture automatically after 0.85 seconds of a steady rectangle, settled
  focus/exposure, and sufficient preview detail, or tap the shutter deliberately
  for each physical copy. Focus/zoom changes and soft frames restart that interval.
- The detail guard measures three interior bands of a perspective-corrected card
  after smoothing noise. It rejects severe softness and blank frames; it is a
  heuristic, not proof of readable text or correct recognition. Glare, low contrast,
  and unusual art can still require the manual shutter and better lighting.
- Automatic capture rearms only after the card has been absent for at least
  0.9 seconds. A brief missed detection or camera shake does not rearm it.
- Still images retain their orientation and are limited to a 3840-pixel longest
  side, encoded at 96% JPEG quality. Unusually large imports reduce JPEG quality
  if necessary to stay below the server's 15 MB limit. The app does not use the
  PC as a camera bridge.
- Native uploads explicitly identify a full photo so the server locates the
  physical card, while preserving the website's already-cropped card frames.
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
   Compare the live preview and uploaded review photo with the stock Camera app
   at the same distance. Try tap-to-focus, backing away with zoom, and Close-up
   if offered. A glossy sleeve needs light angled away from the camera.
3. Verify there is one receipt per capture and no stationary-card duplicate.
4. Interrupt Wi-Fi during an upload, quit/reopen, retry, and verify the quantity.
5. Resolve a review, edit collection/deck details, and export through the share sheet.
6. Background the app and change tabs; the camera must stop outside Scan.

Physical-device acceptance is pending until these checks are performed.

Camera implementation references: [Apple's minimum focus distance guidance](https://developer.apple.com/videos/play/wwdc2021/10047/),
[virtual-camera zoom factors](https://developer.apple.com/documentation/avfoundation/avcapturedevice/virtualdeviceswitchovervideozoomfactors),
and [preview-to-focus point conversion](https://developer.apple.com/documentation/avfoundation/avcapturevideopreviewlayer/capturedevicepointconverted(fromlayerpoint:)).
