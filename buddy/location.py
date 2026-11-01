"""Where the laptop is, from the Wi-Fi networks around it or else its internet connection.

Laptops rarely have GPS, so this asks beaconDB, and only when the user asks for their location.
"""
import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from . import apps

LOOKUP_URL = "https://api.beacondb.net/v1/geolocate"
USER_AGENT = "Buddy-voice-control/1.0"  # beaconDB asks every client to identify itself
LOOKUP_SECONDS = 15
SCAN_SECONDS = 15
MIN_NETWORKS = 2  # one network alone can't be told apart from a hotspot that has moved
MAX_NETWORKS = 20
ROUGH_METRES = 5000
_NETWORK = re.compile(r"(.*):((?:[0-9A-Fa-f]{2}\\:){5}[0-9A-Fa-f]{2}):(\d+)")


@dataclass
class Place:
    lat: float
    lon: float
    metres: float
    rough: bool

    @property
    def link(self) -> str:
        return f"https://www.google.com/maps?q={self.lat:.5f},{self.lon:.5f}"

    @property
    def accuracy(self) -> str:
        if self.rough:
            return "This is only a rough guess from the internet connection, so it could be a different area or city"
        if self.metres < 1000:
            return f"Accurate to about {round(self.metres, -1):.0f} metres"
        return f"Accurate to about {self.metres / 1000:.0f} km"


def nearby_networks() -> list[dict]:
    """The strongest networks in range, leaving out any that opted out or may be moving around."""
    try:
        listing = subprocess.run(["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL", "device", "wifi", "list"],
                                 capture_output=True, text=True, timeout=SCAN_SECONDS, env=apps.session_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    networks = []
    for line in listing.splitlines():
        if not (match := _NETWORK.fullmatch(line)):
            continue
        name, address, signal = match.group(1), match.group(2).replace("\\", "").lower(), int(match.group(3))
        if name.endswith("_nomap") or int(address[:2], 16) & 2:  # asks not to be mapped, or a made-up address like a phone's
            continue
        networks.append({"macAddress": address, "signalStrength": signal // 2 - 100})  # nmcli gives 0-100, this wants dBm
    networks.sort(key=lambda network: network["signalStrength"], reverse=True)
    return networks[:MAX_NETWORKS] if len(networks) >= MIN_NETWORKS else []


def find() -> Place:
    """With no usable networks the service goes by the internet connection alone."""
    networks = nearby_networks()
    body = {"wifiAccessPoints": networks} if networks else {}
    request = urllib.request.Request(LOOKUP_URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=LOOKUP_SECONDS) as response:
            reply = json.load(response)
        lat, lon, metres = float(reply["location"]["lat"]), float(reply["location"]["lng"]), float(reply["accuracy"])
    except (OSError, ValueError, KeyError, TypeError) as error:  # urllib's errors are all OSError
        raise RuntimeError("I couldn't find the laptop's location. Check the internet connection and try again") from error
    return Place(lat, lon, metres, rough=bool(reply.get("fallback")) or not networks or metres >= ROUGH_METRES)


def message(place: Place) -> str:
    return f"The laptop's location at {datetime.now():%-I:%M %p}:\n{place.link}\n{place.accuracy}."
