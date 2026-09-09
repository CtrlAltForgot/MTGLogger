MTGLogger 1.0.1 improves the native scanner's camera focus and capture controls.

- Tap the card to focus and expose at that point, with a visible focus ring.
- Auto can select the close-focus camera on supported iPhones. The lens menu
  also offers Main and an autofocus Close-up lens when available.
- Suggested zoom leaves more working distance for phones whose main lens cannot
  focus close to a card. The slider and reset button allow manual adjustment.
- Automatic capture waits for steady, settled, sufficiently detailed frames.
  A focus hunt or soft frame restarts the countdown. Manual capture remains available.
- Still photos retain up to 3840 pixels on their longest side at 96% JPEG quality.

Install the IPA over the existing app using SideStore, or refresh the MTGLogger
source and update. Check Settings for version 1.0.1. Requires iOS 17 or later and
the updated MTGLogger server. The bundle identifier is unchanged for updates.

In Scan, tap the card, move back if the text is soft, and adjust zoom to fill the
guide. Try Close-up if the lens menu offers it. Use even light and tilt reflective
sleeves away from glare.

The release workflow requires simulator tests and an ARM64 build before packaging.
Those checks cannot establish physical camera quality. Autofocus, lens switching,
and the preview detail heuristic still need verification on the user's phone;
the detail score does not guarantee readable text or correct identification.
