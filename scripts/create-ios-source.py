"""Generate a SideStore source from the exact built IPA, without notarization fields."""
import argparse
import hashlib
import json
import plistlib
import zipfile
from datetime import UTC, datetime
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("ipa", type=Path)
parser.add_argument("--base-url", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
with zipfile.ZipFile(args.ipa) as archive:
    info_name = next(name for name in archive.namelist() if name.count("/") == 2 and name.endswith(".app/Info.plist"))
    info = plistlib.loads(archive.read(info_name))
base = args.base_url.rstrip("/")
source = {
    "name": "MTGLogger",
    "identifier": "dev.ctrlaltforgot.mtglogger.source",
    "subtitle": "Your collection. Your camera.",
    "description": "Scan Magic cards directly into your own MTGLogger server.",
    "iconURL": "https://raw.githubusercontent.com/CtrlAltForgot/MTGLogger/main/frontend/public/mtglogger-512.png",
    "website": "https://github.com/CtrlAltForgot/MTGLogger",
    "tintColor": "E84057",
    "apps": [{
        "name": "MTGLogger",
        "bundleIdentifier": info["CFBundleIdentifier"],
        "developerName": "CtrlAltForgot",
        "subtitle": "Scan, collect, review, and organize",
        "localizedDescription": "Native rear-camera scanning with steady-card capture, saved upload retries, and the full MTGLogger collection, review, decks, value, database, play, and export workflows. Connect to your existing server from Settings.",
        "iconURL": "https://raw.githubusercontent.com/CtrlAltForgot/MTGLogger/main/frontend/public/mtglogger-512.png",
        "tintColor": "E84057",
        "versions": [{
            "version": info["CFBundleShortVersionString"],
            "date": datetime.now(UTC).isoformat(),
            "localizedDescription": "Native camera scanner and full collection companion. Requires the updated MTGLogger server with capture retry support.",
            "downloadURL": f"{base}/MTGLogger.ipa",
            "size": args.ipa.stat().st_size,
            "minOSVersion": info.get("MinimumOSVersion", "17.0"),
        }],
        "appPermissions": {
            "entitlements": [],
            "privacy": {
                "NSCameraUsageDescription": info["NSCameraUsageDescription"],
                "NSLocalNetworkUsageDescription": info["NSLocalNetworkUsageDescription"],
            },
        },
    }],
    "news": [],
}
args.output.write_text(json.dumps(source, indent=2) + "\n")
args.ipa.with_suffix(".ipa.sha256").write_text(
    f"{hashlib.sha256(args.ipa.read_bytes()).hexdigest()}  {args.ipa.name}\n"
)
