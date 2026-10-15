"""Deliver alert notifications using local Windows notification channels."""

import base64
import ctypes
import platform
import subprocess
import sys
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

from notifications.notification_queue import Notification
from notifications.sound_settings import NotificationSoundSettings, SoundSettingsError


class NotificationHandler:
    """Deliver one notification with a sound and a Windows desktop toast."""

    def __init__(
        self, sound_settings: NotificationSoundSettings | None = None
    ) -> None:
        self.sound_settings = sound_settings or NotificationSoundSettings()

    def handle(self, notification: Notification) -> None:
        """Attempt each delivery channel independently."""
        for channel, deliver in (
            ("sound", self._play_sound),
            ("desktop notification", self._show_desktop_notification),
        ):
            try:
                deliver(notification)
            except Exception as error:
                print(
                    f"Notification {channel} failed for "
                    f"{notification.alert.alert_id}: {error}",
                    file=sys.stderr,
                )

    def play_current_sound(self) -> None:
        """Play the selected alert sound the configured number of times."""
        path, count = self.sound_settings.playback_target()
        try:
            if path is not None and path.suffix.casefold() == ".mp3":
                for _ in range(count):
                    self._play_mp3(path)
            else:
                import winsound

                for _ in range(count):
                    if path is None:
                        winsound.PlaySound(
                            "SystemExclamation", winsound.SND_ALIAS
                        )
                    else:
                        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        except SoundSettingsError:
            raise
        except Exception:
            raise RuntimeError("Could not play the selected alert sound.") from None

    @staticmethod
    def _play_mp3(path: Path) -> None:
        """Play one MP3 synchronously through Windows' built-in MCI interface."""
        sound_path = str(path)
        if '"' in sound_path:
            raise RuntimeError("Could not play the selected alert sound.")
        alias = f"alert_sound_{uuid.uuid4().hex}"
        winmm = ctypes.WinDLL("winmm")
        send_command = winmm.mciSendStringW
        send_command.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_void_p,
        ]
        send_command.restype = ctypes.c_uint
        if send_command(
            f'open "{sound_path}" type mpegvideo alias {alias}',
            None,
            0,
            None,
        ):
            raise RuntimeError("Could not play the selected alert sound.")
        try:
            if send_command(f"play {alias} wait", None, 0, None):
                raise RuntimeError("Could not play the selected alert sound.")
        finally:
            send_command(f"close {alias}", None, 0, None)

    def _play_sound(self, notification: Notification) -> None:
        """Play the selected sound for a triggered alert."""
        del notification
        self.play_current_sound()

    @staticmethod
    def _show_desktop_notification(notification: Notification) -> None:
        """Show a Windows toast through the built-in PowerShell WinRT APIs."""
        if platform.system() != "Windows":
            raise OSError("Windows desktop notifications are unavailable")

        title = (
            "Market Alert Recovered"
            if notification.recovered
            else "Market Alert Triggered"
        )
        message = notification.message
        toast_xml = (
            "<toast><visual><binding template=\"ToastGeneric\">"
            f"<text>{escape(title)}</text>"
            f"<text>{escape(message)}</text>"
            "</binding></visual></toast>"
        )
        encoded_xml = base64.b64encode(toast_xml.encode("utf-8")).decode(
            "ascii"
        )
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "$ProgressPreference = 'SilentlyContinue'; "
            "try { "
            "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
            "$null = [Windows.UI.Notifications.ToastNotificationManager, "
            "Windows.UI.Notifications, ContentType = WindowsRuntime]; "
            "$xml = [Windows.Data.Xml.Dom.XmlDocument, "
            "Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]::new(); "
            "$toastXml = [System.Text.Encoding]::UTF8.GetString("
            f"[System.Convert]::FromBase64String('{encoded_xml}')); "
            "$xml.LoadXml($toastXml); "
            "$toast = [Windows.UI.Notifications.ToastNotification, "
            "Windows.UI.Notifications, ContentType = WindowsRuntime]::new($xml); "
            "$notifier = [Windows.UI.Notifications.ToastNotificationManager]"
            "::CreateToastNotifier('Windows.SystemToast.Explorer'); "
            "if ([string]$notifier.Setting -ne 'Enabled') "
            "{ throw \"Windows notifications are not enabled for Explorer "
            "(setting: $($notifier.Setting))\" }; "
            "$notifier.Show($toast); "
            "exit 0 "
            "} catch { [Console]::Error.WriteLine($_.ToString()); exit 1 }"
        )
        encoded_script = base64.b64encode(
            script.encode("utf-16le")
        ).decode("ascii")
        result = subprocess.run(
            [
                "powershell.exe",
                "-WindowStyle",
                "Hidden",
                "-NoProfile",
                "-NonInteractive",
                "-OutputFormat",
                "Text",
                "-EncodedCommand",
                encoded_script,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode:
            details = result.stderr.strip() or result.stdout.strip()
            details = details[:1000]
            raise RuntimeError(details or "PowerShell toast command failed")
