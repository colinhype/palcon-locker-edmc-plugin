#!/usr/bin/env python3
# PalConLocker EDMC Plugin
# Save as:
#   EDMC/Plugins/PalConLocker/load.py

import os
import json
import threading
import requests
import tkinter as tk
import time
from datetime import datetime, timezone
from collections import deque

try:
    from edmc_logging import getLogger
except ImportError:
    import logging
    def getLogger(name):
        return logging.getLogger(name)

log = getLogger(__name__)

__author__ = "Colinhype"
__version__ = "0.8.2"
__description__ = "Feeds PalCon Locker CMDR activity via PHP API, with DB-backed sector watchlists."

NOTIFY_URL = "https://palconlocker.com/api/notify.php"
SECTORS_URL = "https://palconlocker.com/api/sectors.php"
ACTIVITY_WEBHOOK_URL = "https://discord.com/api/webhooks/1514416142972747776/-j2Y2-VWrxO7Lg3gGVV1R-GZcAOxmMyexlXfFVFrCDV6eYbER0B5RpAWRBDB7_BNqVmP"
LATEST_URL = "https://palconlocker.com/latest.json"

PLUGIN_DIR = os.path.dirname(__file__)
SETTINGS_FILE = os.path.join(PLUGIN_DIR, "palcon_settings.json")
TRACKED_MISSIONS_FILE = os.path.join(PLUGIN_DIR, "palcon_tracked_missions.json")

watched_systems = set()
all_sectors = {}
tracked_missions = {}
status_light = None
status_label = None
last_sector_refresh = 0
last_version_check = 0
status_queue = deque()
status_busy = False

STATUS_CONNECTED = "connected"
STATUS_UPLOADING = "uploading"
STATUS_FAILED = "failed"


def load_settings():
    global all_sectors, watched_systems

    try:
        resp = requests.get(SECTORS_URL, timeout=5)
        resp.raise_for_status()
        all_sectors = resp.json() or {}
        log.info("PalConLocker: fetched %s sectors from remote.", len(all_sectors))
    except Exception as e:
        log.error("PalConLocker: failed fetching sectors.json: %s", e)
        all_sectors = {}

    selected = list(all_sectors.keys())

    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                selected = cfg.get("selected_sectors", selected)
        except Exception as e:
            log.error("PalConLocker: error reading settings: %s", e)

    watched_systems = {
        str(sys).strip().lower()
        for sector in selected
        for sys in all_sectors.get(sector, [])
        if str(sys).strip()
    }

    log.info(
        "PalConLocker: watching %s systems across %s sectors.",
        len(watched_systems),
        len(selected)
    )


def save_settings(selected_sectors):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"selected_sectors": selected_sectors}, f, indent=2)
        load_settings()
    except Exception as e:
        log.error("PalConLocker: failed to save settings: %s", e)


def load_tracked_missions():
    global tracked_missions

    if not os.path.exists(TRACKED_MISSIONS_FILE):
        tracked_missions = {}
        return

    try:
        with open(TRACKED_MISSIONS_FILE, "r", encoding="utf-8") as f:
            tracked_missions = json.load(f) or {}
    except Exception as e:
        log.error("PalConLocker: failed to load tracked missions: %s", e)
        tracked_missions = {}


def save_tracked_missions():
    try:
        with open(TRACKED_MISSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(tracked_missions, f, indent=2)
    except Exception as e:
        log.error("PalConLocker: failed to save tracked missions: %s", e)


def mission_key(entry):
    mission_id = entry.get("MissionID")
    return str(mission_id) if mission_id is not None else None


def remember_mission(entry, accepted_system):
    key = mission_key(entry)

    if not key:
        return

    tracked_missions[key] = {
        "accepted_system": accepted_system,
        "faction": entry.get("Faction", "N/A"),
        "mission_type": entry.get("LocalisedName") or entry.get("Name") or "Mission Accepted",
        "timestamp": entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
    }

    save_tracked_missions()


def get_tracked_mission(entry):
    key = mission_key(entry)

    if not key:
        return None

    return tracked_missions.get(key)


def forget_tracked_mission(entry):
    key = mission_key(entry)

    if key and key in tracked_missions:
        tracked_missions.pop(key, None)
        save_tracked_missions()


def plugin_app(parent):
    global status_light, status_label

    frame = tk.Frame(parent)

    status_light = tk.Label(
        frame,
        width=2,
        height=1,
        bg="#00cc44"
    )
    status_light.pack(side="left", padx=(0, 5))

    status_label = tk.Label(
        frame,
        text="Connected to PalConLocker"
    )
    status_label.pack(side="left")

    return frame
    
def set_status(state):
    global status_light, status_label

    if not status_light or not status_label:
        return

    try:
        if state == STATUS_CONNECTED:
            status_light.configure(bg="#00cc44")
            status_label.configure(text="Connected to PalConLocker")

        elif state == STATUS_UPLOADING:
            status_light.configure(bg="#ffb300")
            status_label.configure(text="Uploading activity")

        elif state == STATUS_FAILED:
            status_light.configure(bg="#ff3333")
            status_label.configure(text="Upload failed")

    except Exception:
        pass


def flash_upload():
    if not status_light:
        return

    try:
        status_light.configure(bg="#ffb300")
        status_light.after(250, lambda: status_light.configure(bg="#333333"))
        status_light.after(500, lambda: status_light.configure(bg="#00cc44"))
    except Exception:
        pass

def send_activity_webhook(payload):
    event = payload.get("event")

    # Don't send jumps to the activity Discord channel.
    if event == "FSDJump":
        return

    cmdr = payload.get("cmdr", "Unknown CMDR")
    system = payload.get("system", "Unknown system")
    faction = payload.get("faction")
    mission_type = payload.get("mission_type")
    commodity = payload.get("commodity")
    amount = payload.get("amount")
    merits = payload.get("merits")
    total_earnings = payload.get("total_earnings")

    title_map = {
        "MissionAccepted": "📋 Mission accepted",
        "MissionCompleted": "✅ Mission completed",
        "MissionFailed": "❌ Mission failed",
        "MissionAbandoned": "⚠️ Mission abandoned",
        "MarketBuy": "🛒 Market buy",
        "MarketSell": "💰 Market sell",
        "RedeemVoucher": "🎯 Voucher redeemed",
        "Bounty": "🎯 Bounty claimed",
        "FactionKillBond": "⚔️ Combat bond",
        "CombatBond": "⚔️ Combat bond",
        "SellExplorationData": "🛰️ Exploration data sold",
        "MultiSellExplorationData": "🛰️ Exploration data sold",
        "SellOrganicData": "🌱 Organic data sold",
        "PowerplayMerits": "🏛️ Powerplay merits",
        "PowerplayCollect": "📦 Powerplay collect",
        "PowerplayDeliver": "📦 Powerplay deliver",
        "PowerplayVoucher": "🏛️ Powerplay voucher",
        "CarrierStats": "🚢 Carrier stats",
    }

    title = title_map.get(event, event)

    lines = [
        f"**{title}**",
        f"CMDR: **{cmdr}**",
        f"System: **{system}**",
    ]

    if faction:
        lines.append(f"Faction: **{faction}**")

    if mission_type:
        lines.append(f"Mission: {mission_type}")

    if commodity:
        trade_line = f"Commodity: **{commodity}**"
        if amount:
            trade_line += f" x{amount}"
        lines.append(trade_line)

    if merits:
        lines.append(f"Merits: **{merits}**")

    if total_earnings:
        try:
            credits = f"{int(total_earnings):,}"
        except Exception:
            credits = str(total_earnings)

        lines.append(f"Value: **{credits} cr**")

    content = "\n".join(lines)

    try:
        requests.post(
            ACTIVITY_WEBHOOK_URL,
            json={"content": content},
            timeout=5
        )
    except Exception as e:
        log.error("PalConLocker: activity webhook failed: %s", e)

def queue_status(message):
    global status_busy

    status_queue.append(message)

    if not status_busy:
        show_next_status()


def show_next_status():
    global status_busy

    if not status_label:
        status_busy = False
        return

    if not status_queue:
        status_busy = False
        status_label.configure(text="Connected to PalConLocker")
        return

    status_busy = True
    status_label.configure(text=status_queue.popleft())
    status_label.after(5000, show_next_status)

def notify_api(
    cmdr,
    system,
    event,
    faction=None,
    influence=None,
    timestamp=None,
    mission_type=None,
    merits=None,
    commodity=None,
    amount=None,
    price=None,
    trade_type=None,
    total_earnings=None,
    progress=None,
):
    payload = {
        "cmdr": cmdr,
        "system": system,
        "event": event,
        "faction": faction,
        "influence": influence,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "mission_type": mission_type,
        "merits": merits,
        "commodity": commodity,
        "amount": amount,
        "price": price,
        "trade_type": trade_type,
        "total_earnings": total_earnings,
        "progress": progress,
    }

    payload = {k: v for k, v in payload.items() if v is not None}
    send_activity_webhook(payload)
    flash_upload()

    try:
        r = requests.post(NOTIFY_URL, json=payload, timeout=5)

        if r.ok:
            if status_light:
                status_light.configure(bg="#00cc44")

            message = f"✓ {event} uploaded"

            if event == "MissionAccepted":
                message = f"✓ Accepted: {mission_type}"

            elif event == "MissionCompleted":
                message = f"✓ Completed: {mission_type}"

            elif event in ("RedeemVoucher", "Bounty"):
                value = int(amount or total_earnings or 0)
                message = f"✓ Cashed {value:,} cr bounties"
                if faction:
                    message += f" ({faction})"

            elif event in ("FactionKillBond", "CombatBond"):
                value = int(amount or total_earnings or 0)
                message = f"✓ Handed in {value:,} cr bonds"
                if faction:
                    message += f" ({faction})"

            elif event == "MarketSell":
                message = f"✓ Sold {amount or 0}t {commodity or ''}".strip()

            elif event == "MarketBuy":
                message = f"✓ Bought {amount or 0}t {commodity or ''}".strip()

            elif event in ("SellExplorationData", "MultiSellExplorationData"):
                value = int(total_earnings or amount or 0)
                message = f"✓ Sold exploration data ({value:,} cr)"

            elif event == "SellOrganicData":
                value = int(total_earnings or amount or 0)
                message = f"✓ Sold organic data ({value:,} cr)"

            elif event.startswith("Powerplay"):
                value = merits or amount or progress or 0
                message = f"✓ Uploaded {value} merits"

            queue_status(message)

        else:
            set_status(STATUS_FAILED)
            log.error(
                "PalConLocker: notify failed %s %s | payload=%s",
                r.status_code,
                r.text,
                payload
            )

    except Exception as e:
        set_status(STATUS_FAILED)
        log.error("PalConLocker: exception sending notify: %s", e)

def check_version():
    try:
        r = requests.get(LATEST_URL, timeout=5)

        if not r.ok:
            return

        data = r.json() or {}

        latest = str(data.get("version") or "").strip()
        message = data.get("message") or ""
        download = data.get("download_url") or ""

        if not latest:
            return

        if latest != __version__:

            if status_label:
                if message:
                    status_label.configure(text=f"📢 {message}")
                else:
                    status_label.configure(text=f"📢 Update available: v{latest}")

            log.info(
                "PalConLocker: update available v%s (%s)",
                latest,
                download
            )

    except Exception as e:
        log.error("PalConLocker: version check failed: %s", e)


def mission_effect_summary(entry):
    """
    Convert Frontier's FactionEffects JSON into a clean readable summary.

    Example:
    Mission: Courier Job Available
    Influence: +++
    Rep: +
    """

    lines = []

    for fe in entry.get("FactionEffects", []) or []:
        influence_entries = fe.get("Influence", [])
        reputation = fe.get("Reputation")

        if isinstance(influence_entries, list):
            for influence_entry in influence_entries:
                if not isinstance(influence_entry, dict):
                    continue

                influence_value = influence_entry.get("Influence")

                if influence_value:
                    lines.append("Influence: " + str(influence_value))
                    break
        elif influence_entries:
            lines.append("Influence: " + str(influence_entries))

        if reputation:
            lines.append("Rep: " + str(reputation))

    # Remove duplicates while preserving order.
    cleaned = []
    seen = set()

    for line in lines:
        if line in seen:
            continue

        cleaned.append(line)
        seen.add(line)

    return "\n".join(cleaned) if cleaned else None


def journal_entry(cmdr, is_beta, system, station, entry, state):
    global last_sector_refresh

    # Refresh watched systems every 30 minutes
    if time.time() - last_sector_refresh > 1800:
        try:
            load_settings()
            last_sector_refresh = time.time()
            log.info("PalConLocker: refreshed sectors from server.")
        except Exception as e:
            log.error("PalConLocker: sector refresh failed: %s", e)
    # Check for plugin updates once per day
    if time.time() - globals().get("last_version_check", 0) > 86400:
        try:
            check_version()
            globals()["last_version_check"] = time.time()
        except Exception as e:
            log.error("PalConLocker: version check failed: %s", e)

    event = entry.get("event")

    tracked_events = {
        "FSDJump",
        "MissionAccepted",
        "MissionCompleted",
        "MarketBuy",
        "MarketSell",
        "RedeemVoucher",
        "Bounty",
        "FactionKillBond",
        "PowerplayMerits",
        "PowerplayCollect",
        "PowerplayDeliver",
        "SellExplorationData",
        "MultiSellExplorationData",
        "SellOrganicData",
        "MissionFailed",
        "MissionAbandoned",
        "CombatBond",
        "PowerplayVoucher",
        "CarrierStats"
    }

    if event not in tracked_events:
        return

    ts = entry.get("timestamp", datetime.now(timezone.utc).isoformat())
    current_system = system or entry.get("StarSystem") or entry.get("System") or ""
    log.info("PalConLocker debug: event=%s system=%s entry=%s", event, current_system, entry)
    
    if event == "FSDJump":
        sys_name = entry.get("StarSystem", "").strip()
        sys_key = sys_name.lower()

        if sys_key not in watched_systems:
            return

        factions = entry.get("Factions", [])
        top = max(factions, key=lambda f: float(f.get("Influence", 0)), default=None)

        if top:
            faction = top.get("Name", "N/A")
            influence = round(float(top.get("Influence", 0)) * 100, 1)
        else:
            faction, influence = "N/A", 0.0

        threading.Thread(
            target=notify_api,
            kwargs={
                "cmdr": cmdr,
                "system": sys_name.title(),
                "event": "FSDJump",
                "faction": faction,
                "influence": influence,
                "timestamp": ts,
            },
            daemon=True
        ).start()
        return
        
    if event == "MissionAccepted":
        if not current_system:
            return

        faction = entry.get("Faction", "N/A")
        mission_name = entry.get("LocalisedName") or entry.get("Name") or "Mission Accepted"
        reward = entry.get("Donation") or entry.get("Reward") or 0

        remember_mission(entry, current_system)

        threading.Thread(
            target=notify_api,
            kwargs={
                "cmdr": cmdr,
                "system": current_system,
                "event": "MissionAccepted",
                "faction": faction,
                "timestamp": ts,
                "mission_type": mission_name,
                "total_earnings": reward,
            },
            daemon=True
        ).start()
        return

    if event == "MissionCompleted":
        tracked = get_tracked_mission(entry)

        faction = entry.get("Faction") or (tracked or {}).get("faction", "N/A")

        mission_name = (
            entry.get("LocalisedName")
            or entry.get("Name")
            or (tracked or {}).get("mission_type")
            or "Mission Completed"
        )

        effects = mission_effect_summary(entry)
        if effects:
            mission_name = mission_name + "\n" + effects

        accepted_system = (tracked or {}).get("accepted_system")
        display_system = accepted_system or current_system

        reward = entry.get("Reward") or entry.get("Donation") or entry.get("Donated") or 0

        threading.Thread(
            target=notify_api,
            kwargs={
                "cmdr": cmdr,
                "system": display_system,
                "event": "MissionCompleted",
                "faction": faction,
                "timestamp": ts,
                "mission_type": mission_name,
                "total_earnings": reward,
            },
            daemon=True
        ).start()

        forget_tracked_mission(entry)
        return

    if event in ("MissionFailed", "MissionAbandoned"):
        tracked = get_tracked_mission(entry)

        threading.Thread(
            target=notify_api,
            kwargs={
                "cmdr": cmdr,
                "system": current_system or (tracked or {}).get("accepted_system", ""),
                "event": event,
                "faction": entry.get("Faction") or (tracked or {}).get("faction", "N/A"),
                "timestamp": ts,
                "mission_type": entry.get("LocalisedName") or entry.get("Name") or (tracked or {}).get("mission_type"),
            },
            daemon=True
        ).start()

        forget_tracked_mission(entry)
        return

    # Non-mission activity can be useful anywhere.
    # This lets PalCon see commanders working in COPI, Colonia, unsectored systems, etc.
    if event != "FSDJump" and not current_system:
        return

    if event == "MarketBuy":
        threading.Thread(
            target=notify_api,
            kwargs={
                "cmdr": cmdr,
                "system": current_system,
                "event": "MarketBuy",
                "timestamp": ts,
                "commodity": entry.get("Type") or entry.get("Commodity"),
                "amount": entry.get("Count"),
                "price": entry.get("BuyPrice") or entry.get("Price"),
                "trade_type": "buy",
                "total_earnings": entry.get("TotalCost"),
            },
            daemon=True
        ).start()
        return

    if event == "MarketSell":
        threading.Thread(
            target=notify_api,
            kwargs={
                "cmdr": cmdr,
                "system": current_system,
                "event": "MarketSell",
                "timestamp": ts,
                "commodity": entry.get("Type") or entry.get("Commodity"),
                "amount": entry.get("Count"),
                "price": entry.get("SellPrice") or entry.get("Price"),
                "trade_type": "sell",
                "total_earnings": entry.get("TotalSale"),
            },
            daemon=True
        ).start()
        return

    if event in ("RedeemVoucher", "Bounty", "FactionKillBond"):
        threading.Thread(
            target=notify_api,
            kwargs={
                "cmdr": cmdr,
                "system": current_system,
                "event": event,
                "faction": entry.get("Faction") or entry.get("VictimFaction") or entry.get("TargetFaction"),
                "timestamp": ts,
                "amount": entry.get("Amount") or entry.get("Reward") or entry.get("TotalReward"),
            },
            daemon=True
        ).start()
        return
        
    if event in ("SellExplorationData", "MultiSellExplorationData", "SellOrganicData"):
        threading.Thread(
            target=notify_api,
            kwargs={
                "cmdr": cmdr,
                "system": current_system,
                "event": event,
                "timestamp": ts,
                "total_earnings": (
                    entry.get("TotalEarnings")
                    or entry.get("Reward")
                    or entry.get("Amount")
                ),
            },
            daemon=True
        ).start()
        return

    if event in ("PowerplayMerits", "PowerplayCollect", "PowerplayDeliver"):
        threading.Thread(
            target=notify_api,
            kwargs={
                "cmdr": cmdr,
                "system": current_system,
                "event": event,
                "faction": entry.get("Power") or entry.get("Faction"),
                "timestamp": ts,
                "merits": entry.get("MeritsGained") or entry.get("Merits") or entry.get("Amount"),
                "commodity": entry.get("Type") or entry.get("Commodity"),
                "amount": entry.get("Count"),
            },
            daemon=True
        ).start()
        return


def plugin_start3(plugin_dir):
    global last_sector_refresh
    
    load_settings()
    load_tracked_missions()
    last_sector_refresh = 0
    log.info("PalConLocker: plugin initialized v%s", __version__)
    return "PalCon Locker"


plugin_start = plugin_start3
