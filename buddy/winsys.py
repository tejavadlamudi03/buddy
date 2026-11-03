"""Windows' own ways of doing what GNOME's tools do on Linux. Imports safely anywhere, but only works on Windows."""
import asyncio
import ctypes
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config

COM_SECONDS = 15
# The panels of actions._SETTINGS_PANELS, as pages of the Windows Settings app.
SETTINGS_PAGES = {
    "wifi": "network-wifi", "network": "network", "bluetooth": "bluetooth", "sound": "sound", "power": "powersleep",
    "display": "display", "background": "personalization-background", "ubuntu": "personalization",
    "notifications": "notifications", "search": "search", "multitasking": "multitasking", "applications": "appsfeatures",
    "privacy": "privacy", "online-accounts": "emailandaccounts", "sharing": "crossdevice", "mouse": "mousetouchpad",
    "keyboard": "typing", "printers": "printers", "removable-media": "autoplay", "color": "personalization-colors",
    "universal-access": "easeofaccess", "system": "about",
}
# No public way to flip these, so Buddy takes the user to the switch rather than pretend.
ONLY_IN_SETTINGS = {"airplane": ("Airplane mode", "network-airplanemode"), "night_light": ("Night light", "nightlight"),
                    "do_not_disturb": ("Do not disturb", "notifications")}
POWER_PLANS = {"performance": "SCHEME_MIN", "balanced": "SCHEME_BALANCED", "power-saver": "SCHEME_MAX"}  # yes, MIN is the fast one
KNOWN_FOLDERS = {"downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
                 "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
                 "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}", "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}"}
READ_ONLY_COMMANDS = {"whoami", "hostname", "tasklist", "systeminfo", "driverquery", "where", "nvidia-smi"}
EMPTY_BIN_ALREADY = -2147418113  # what Windows answers when the Recycle Bin has nothing in it
ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
# Its own small process, so that like on Linux the block ends by itself and a crash can't leave it on.
KEEP_AWAKE = "import ctypes,sys,time; ctypes.windll.kernel32.SetThreadExecutionState(int(sys.argv[1])); time.sleep(float(sys.argv[2]))"
START_APPS = r"""
$shell = New-Object -ComObject WScript.Shell
$targets = @{}
$menus = "$env:ProgramData\Microsoft\Windows\Start Menu\Programs", "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
Get-ChildItem $menus -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
    $targets[$_.BaseName] = $shell.CreateShortcut($_.FullName).TargetPath
}
@(Get-StartApps | ForEach-Object { @{name = $_.Name; id = $_.AppID; target = $targets[$_.Name]} }) | ConvertTo-Json -Compress
"""
_NETWORK_NAME = re.compile(r"^SSID \d+[ \t]*:[ \t]*(.*)$", re.M)
_NETWORK_BASE = re.compile(r"BSSID \d+\s*:\s*((?:[0-9a-f]{2}:){5}[0-9a-f]{2})\s*\n[^\n]*?(\d+)\s*%", re.I)
_CONNECTED_TO = re.compile(r"^[ \t]*SSID[ \t]*:[ \t]*(.+?)[ \t]*$", re.M)


def _enter_apartment() -> None:
    sys.coinit_flags = 0  # multithreaded, read when comtypes is imported, and what WinRT needs as well
    import comtypes  # noqa: F401


_com = ThreadPoolExecutor(max_workers=1, thread_name_prefix="windows-com", initializer=_enter_apartment)


def on_com_thread(work):
    """Windows' audio and media objects belong to the thread that made them, and Buddy's commands each get their own."""
    return _com.submit(work).result(COM_SECONDS)


def powershell(script: str, timeout: float = 30) -> str:
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); " + script],  # or names in Telugu would come back broken
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, **config.NO_WINDOW)
    if done.returncode:
        raise RuntimeError(done.stderr.strip()[:300] or "PowerShell failed")
    return done.stdout


def start_apps() -> list[dict]:
    """Everything in the Start menu, including Store apps, which have no file to open."""
    return json.loads(powershell(START_APPS, timeout=60) or "[]")


def _end_point(microphone: bool = False):
    from pycaw.pycaw import AudioUtilities
    device = AudioUtilities.CreateDevice(AudioUtilities.GetMicrophone()) if microphone else AudioUtilities.GetSpeakers()
    return device.EndpointVolume


def toggle_mute() -> None:
    def work():
        speakers = _end_point()
        speakers.SetMute(not speakers.GetMute(), None)
    on_com_thread(work)


def set_volume(change: str, level: int | None) -> None:
    def work():
        speakers = _end_point()
        now = speakers.GetMasterVolumeLevelScalar()
        target = {"up": now + 0.1, "down": now - 0.1}.get(change, int(level or 0) / 100)
        speakers.SetMasterVolumeLevelScalar(max(0.0, min(target, 1.0)), None)
        if target > 0:
            speakers.SetMute(False, None)  # or "volume up" would seem to do nothing
    on_com_thread(work)


def _set_radio(wifi: bool, on: bool) -> None:
    from winrt.windows.devices.radios import Radio, RadioAccessStatus, RadioKind, RadioState

    async def work():
        await Radio.request_access_async()
        radios = [radio for radio in await Radio.get_radios_async() if radio.kind == (RadioKind.WI_FI if wifi else RadioKind.BLUETOOTH)]
        if not radios:
            raise LookupError(f"This laptop has no {'Wi-Fi' if wifi else 'Bluetooth'}")
        for radio in radios:
            if await radio.set_state_async(RadioState.ON if on else RadioState.OFF) != RadioAccessStatus.ALLOWED:
                raise PermissionError("Windows didn't let me change that")

    on_com_thread(lambda: asyncio.run(work()))


def _set_dark_mode(on: bool) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", 0, winreg.KEY_SET_VALUE) as key:
        for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if on else 1)
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    send(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 1000, None)  # tells every open app that the theme changed


def _set_microphone(on: bool) -> None:
    on_com_thread(lambda: _end_point(microphone=True).SetMute(not on, None))


def set_option(option: str, on: bool) -> str:
    if option in ONLY_IN_SETTINGS:
        label, page = ONLY_IN_SETTINGS[option]
        os.startfile(f"ms-settings:{page}")
        return f"Windows doesn't let apps switch {label} themselves, so I opened its page for you"
    labels = {"wifi": "Wi-Fi", "bluetooth": "Bluetooth", "dark_mode": "Dark mode", "microphone": "Microphone"}
    if option not in labels:
        raise LookupError(f"I can't change {option} yet")
    if option in ("wifi", "bluetooth"):
        _set_radio(option == "wifi", on)
    elif option == "dark_mode":
        _set_dark_mode(on)
    else:
        _set_microphone(on)
    return f"{labels[option]} is {'on' if on else 'off'}"


def get_brightness() -> int:
    try:
        return int(powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness").split()[0])
    except (RuntimeError, ValueError, IndexError):
        raise RuntimeError("This screen doesn't let Windows change its brightness") from None


def set_brightness(level: int) -> None:
    powershell("Invoke-CimMethod -InputObject (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods) "
               f"-MethodName WmiSetBrightness -Arguments @{{Timeout=1;Brightness=[byte]{int(level)}}} | Out-Null")


def set_power_plan(profile: str) -> None:
    subprocess.run(["powercfg", "/setactive", POWER_PLANS[profile]], check=True, capture_output=True, timeout=5, **config.NO_WINDOW)


def open_settings(panel: str) -> None:
    os.startfile(f"ms-settings:{SETTINGS_PAGES.get(panel, 'about')}")


def known_folder(name: str) -> Path | None:
    """Where this folder really is: OneDrive often moves Documents, Desktop and Pictures."""
    if name not in KNOWN_FOLDERS:
        return None

    class Guid(ctypes.Structure):
        _fields_ = [("bytes", ctypes.c_byte * 16)]

    guid, found = Guid(), ctypes.c_wchar_p()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(KNOWN_FOLDERS[name]), ctypes.byref(guid))
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(found)) != 0:
        return None
    path = Path(found.value)
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free(found)
    return path


def empty_recycle_bin() -> None:
    empty = ctypes.windll.shell32.SHEmptyRecycleBinW
    empty.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    empty.restype = ctypes.c_long
    answer = empty(None, None, 0x7)  # no confirmation, progress window or sound: Buddy already asked
    if answer not in (0, EMPTY_BIN_ALREADY):
        raise OSError(f"Windows couldn't empty the Recycle Bin (error {answer & 0xFFFFFFFF:#x})")


def is_locked() -> bool:
    """At the lock screen, and at a security prompt, a normal program isn't allowed to open the active desktop."""
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    user32.OpenInputDesktop.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    desktop = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not desktop:
        return True
    user32.CloseDesktop(desktop)
    return False


def power(mode: str) -> None:
    if mode == "lock":
        ctypes.windll.user32.LockWorkStation()
    elif mode == "suspend":
        # Not SetSuspendState through rundll32, which hibernates instead on many laptops.
        powershell("Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false) | Out-Null")
    else:
        flag = {"shutdown": ["/s", "/t", "0"], "restart": ["/r", "/t", "0"], "logout": ["/l"]}[mode]
        subprocess.run(["shutdown", *flag], check=True, capture_output=True, timeout=10, **config.NO_WINDOW)


def battery() -> str:
    import psutil
    charge = psutil.sensors_battery()
    if charge is None:
        return "I can't find a battery"
    state = "charging" if charge.power_plugged and charge.percent < 100 else "fully charged" if charge.power_plugged else "discharging"
    return f"Battery is at {round(charge.percent)} percent and {state}"


def processor_line() -> str:
    import psutil
    return f"CPU load {psutil.cpu_percent(interval=0.2):.0f} percent on {os.cpu_count()} threads"


def memory_gb() -> tuple[float, float]:
    """(used, total) in binary gigabytes, as the Linux report gives them."""
    import psutil
    memory = psutil.virtual_memory()
    return (memory.total - memory.available) / 1024 ** 3, memory.total / 1024 ** 3


def connected_wifi(interfaces: str | None = None) -> str:
    if interfaces is None:
        interfaces = _netsh("interfaces")
    match = _CONNECTED_TO.search(interfaces)
    return match.group(1) if match else "not connected"


def _netsh(*what: str) -> str:
    try:
        return subprocess.run(["netsh", "wlan", "show", *what], capture_output=True, text=True, errors="replace", timeout=15,
                              stdin=subprocess.DEVNULL, **config.NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def wifi_networks(listing: str | None = None) -> list[tuple[str, str, int]]:
    """(name, address, signal from 0 to 100) of what's in range. Windows 11 may require Location to be on for this."""
    if listing is None:
        listing = _netsh("networks", "mode=bssid")
    parts = _NETWORK_NAME.split(listing)
    return [(name.strip(), address.lower(), int(signal))
            for name, body in zip(parts[1::2], parts[2::2])
            for address, signal in _NETWORK_BASE.findall(body)]


def keep_awake(seconds: float, screen: bool = False) -> subprocess.Popen:
    state = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | (ES_DISPLAY_REQUIRED if screen else 0)
    return subprocess.Popen([sys.executable, "-c", KEEP_AWAKE, str(state), str(seconds)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **config.NO_WINDOW)


def is_keeping_awake(pid: int) -> bool:
    """By what it runs too, since after a reboot the same number can belong to something else."""
    import psutil
    try:
        return KEEP_AWAKE in psutil.Process(pid).cmdline()
    except psutil.Error:
        return False


def add_to_start_menu(pythonw: Path) -> Path:
    shortcut = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Buddy.lnk"
    quote = lambda text: "'" + str(text).replace("'", "''") + "'"
    powershell(f"$link = (New-Object -ComObject WScript.Shell).CreateShortcut({quote(shortcut)}); "
               f"$link.TargetPath = {quote(pythonw)}; $link.Arguments = '-m buddy'; "
               "$link.Description = 'Control this laptop by voice'; $link.Save()")
    return shortcut


def start_at_login(pythonw: Path) -> None:
    """Under the user's own login, so it needs no administrator and can reach the desktop."""
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Buddy", 0, winreg.REG_SZ, f'"{pythonw}" -m buddy --keep-running')
