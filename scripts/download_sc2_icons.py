"""
Download SC2 icons from Liquipedia to local web/public/icons/sc2/{buildings,upgrades,units}/,
then sync them into the server static dir.

**Run this once after cloning.** 图标是暴雪版权美术，不随仓库分发（见 THIRD_PARTY_NOTICES
第四节），所以 clone 下来是没有的 —— 跑一次本脚本即可。不跑也能玩，只是面板上的建筑/单位/
升级图标会缺图。

Real Liquipedia filename conventions (discovered via API probe):
  Buildings: SC2<CamelCaseName>.jpg  e.g. SC2Nexus.jpg, SC2RoboticsFacility.jpg
  Upgrades: descriptive names         e.g. Ground_weapons_1.gif, Stimpack.png, Blink.png
"""

import json
import sys
import time
from pathlib import Path

import requests

BASE = Path(__file__).parent.parent
BUILDINGS_DIR = BASE / "web" / "public" / "icons" / "sc2" / "buildings"
UPGRADES_DIR = BASE / "web" / "public" / "icons" / "sc2" / "upgrades"
UNITS_DIR = BASE / "web" / "public" / "icons" / "sc2" / "units"
MISSING_TXT = BASE / "web" / "public" / "icons" / "sc2" / "_missing.txt"

BUILDINGS_DIR.mkdir(parents=True, exist_ok=True)
UPGRADES_DIR.mkdir(parents=True, exist_ok=True)
UNITS_DIR.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "VibeCraft-IconFetcher/1.0 (educational)"

LIQUIPEDIA_API = "https://liquipedia.net/starcraft2/api.php"

# ---------------------------------------------------------------------------
# Key -> Liquipedia filename mappings (verified via MediaWiki API)
# ---------------------------------------------------------------------------

# Buildings: SC2<Name>.jpg
BUILDING_FILENAMES: dict[str, str] = {
    "NEXUS": "SC2Nexus.jpg",
    "GATEWAY": "SC2Gateway.jpg",
    "WARPGATE": "SC2WarpGate.jpg",
    "STARGATE": "SC2Stargate.jpg",
    "ROBOTICSFACILITY": "SC2RoboticsFacility.jpg",
    "HATCHERY": "SC2Hatchery.jpg",
    "LAIR": "SC2Lair.jpg",
    "HIVE": "SC2Hive.jpg",
    "COMMANDCENTER": "SC2CommandCenter.jpg",
    "ORBITALCOMMAND": "SC2OrbitalCommand.jpg",
    "PLANETARYFORTRESS": "SC2PlanetaryFortress.jpg",
    "BARRACKS": "SC2Barracks.jpg",
    "FACTORY": "SC2Factory.jpg",
    "STARPORT": "SC2Starport.jpg",
    # 关键科技建筑（tech 行用：只体现有/没有 + 建成/建造中）
    # 神族
    "CYBERNETICSCORE": "SC2CyberneticsCore.jpg",
    "FORGE": "SC2Forge.jpg",
    "TWILIGHTCOUNCIL": "SC2TwilightCouncil.jpg",
    "ROBOTICSBAY": "SC2RoboticsBay.jpg",
    "FLEETBEACON": "SC2FleetBeacon.jpg",
    "TEMPLARARCHIVE": "SC2TemplarArchives.jpg",
    "DARKSHRINE": "SC2DarkShrine.jpg",
    # 虫族
    "SPAWNINGPOOL": "SC2SpawningPool.jpg",
    "ROACHWARREN": "SC2RoachWarren.jpg",
    "BANELINGNEST": "SC2BanelingNest.jpg",
    "EVOLUTIONCHAMBER": "SC2EvolutionChamber.jpg",
    "HYDRALISKDEN": "SC2HydraliskDen.jpg",
    "LURKERDENMP": "SC2LurkerDen.jpg",
    "INFESTATIONPIT": "SC2InfestationPit.jpg",
    "SPIRE": "SC2Spire.jpg",
    "GREATERSPIRE": "SC2GreaterSpire.jpg",
    "ULTRALISKCAVERN": "SC2UltraliskCavern.jpg",
    "NYDUSNETWORK": "SC2NydusNetwork.jpg",
    # 人族
    "ENGINEERINGBAY": "SC2EngineeringBay.jpg",
    "ARMORY": "SC2Armory.jpg",
    "GHOSTACADEMY": "SC2GhostAcademy.jpg",
    "FUSIONCORE": "SC2Fusion Core.jpg",  # 注意：Liquipedia 文件名带空格
}

# Units: <Name>.png convention (verified via imageinfo API probe 2026-06-02)
# Keys = UnitTypeId.name (uppercase), values = Liquipedia wiki filename.
# Units without confirmed Liquipedia icons are NOT listed (frontend uses zh fallback).
# Note: Phoenix.png exists but is a Dota2 asset; no SC2 phoenix icon found.
UNIT_FILENAMES: dict[str, str] = {
    # 神族工人（用 SC2<Name>.jpg 渲染图；裸 Probe.png 是 SC1 精灵图）
    "PROBE": "SC2Probe.jpg",
    # 神族兵种（有图标的）
    "ZEALOT": "SC2Zealot.jpg",  # 裸 Zealot.png 是 SC1 精灵图，用 SC2 渲染图
    "STALKER": "Stalker.png",
    "IMMORTAL": "Immortal.png",
    "HIGHTEMPLAR": "High Templar.png",
    "DARKTEMPLAR": "Dark Templar.png",
    "WARPPRISM": "Warp Prism.png",
    "VOIDRAY": "Void Ray.png",
    "MOTHERSHIP": "Mothership.png",
    # 虫族工人（裸 <Name>.png 多为 SC1 精灵图，已知错的改 SC2<Name>.jpg 渲染图）
    "DRONE": "SC2Drone.jpg",
    # 虫族兵种
    "QUEEN": "SC2Queen.jpg",
    "ZERGLING": "SC2Zergling.jpg",
    "BANELING": "SC2Baneling.jpg",
    "ROACH": "Roach.png",
    "RAVAGER": "Ravager.png",
    "HYDRALISK": "SC2Hydralisk.jpg",
    "LURKERMP": "SC2Lurker.jpg",
    "INFESTOR": "Infestor.png",
    "ULTRALISK": "SC2Ultralisk.jpg",
    "OVERSEER": "Overseer.png",
    "MUTALISK": "SC2Mutalisk.jpg",
    "CORRUPTOR": "Corruptor.png",
    "BROODLORD": "Brood Lord.png",
    "VIPER": "SC2Viper.jpg",
    # 人族工人
    "SCV": "SCV.png",
    # 人族兵种
    "MARINE": "Marine.png",
    "MARAUDER": "Marauder.png",
    "REAPER": "Reaper.png",
    "GHOST": "Ghost.png",
    "HELLION": "Hellion.png",
    "WIDOWMINE": "Widow Mine.png",
    "SIEGETANK": "Siege Tank.png",
    "CYCLONE": "Cyclone.png",
    "THOR": "Thor.png",
    "VIKINGFIGHTER": "Viking.png",
    "MEDIVAC": "Medivac.png",
    "LIBERATOR": "Liberator.png",
    "RAVEN": "Raven.png",
    "BANSHEE": "Banshee.png",
    "BATTLECRUISER": "SC2Battlecruiser.jpg",
    # 有图标但需要确认的（下载失败时前端中文 fallback）
    "DISRUPTOR": "SC2Disruptor.jpg",
}

# Upgrades: real Liquipedia wiki filenames (scraped from race upgrade pages)
UPGRADE_FILENAMES: dict[str, str] = {
    # Protoss ground
    "PROTOSSGROUNDWEAPONSLEVEL1": "Ground_weapons_1.gif",
    "PROTOSSGROUNDWEAPONSLEVEL2": "Ground_weapons_2.gif",
    "PROTOSSGROUNDWEAPONSLEVEL3": "Ground_weapons_3.gif",
    "PROTOSSGROUNDARMORSLEVEL1": "Ground_armor_1.gif",
    "PROTOSSGROUNDARMORSLEVEL2": "Ground_armor_2.gif",
    "PROTOSSGROUNDARMORSLEVEL3": "Ground_armor_3.gif",
    "PROTOSSSHIELDSLEVEL1": "Shields_1.gif",
    "PROTOSSSHIELDSLEVEL2": "Shields_2.gif",
    "PROTOSSSHIELDSLEVEL3": "Shields_3.gif",
    "PROTOSSAIRWEAPONSLEVEL1": "Air_weapons_1.gif",
    "PROTOSSAIRWEAPONSLEVEL2": "Air_weapons_2.gif",
    "PROTOSSAIRWEAPONSLEVEL3": "Air_weapons_3.gif",
    "PROTOSSAIRARMORSLEVEL1": "Air_armor_1.gif",
    "PROTOSSAIRARMORSLEVEL2": "Air_armor_2.gif",
    "PROTOSSAIRARMORSLEVEL3": "Air_armor_3.gif",
    # Protoss units
    "WARPGATERESEARCH": "Transform_warpgate.gif",
    "CHARGE": "Charge.png",
    "BLINKTECH": "Blink.png",
    "ADEPTPIERCINGATTACK": "Resonating_Glaives_(Patch_3.1.2).jpg",
    "PSISTORMTECH": "Psionic_storm.png",
    "HALLUCINATION": "Hallucination.png",
    "OBSERVERGRAVITICBOOSTER": "Gravitic_booster.gif",
    "GRAVITICDRIVE": "Gravitic_drive.gif",
    "EXTENDEDTHERMALLANCE": "Extended_thermal_lances.gif",
    "PHOENIXRANGEUPGRADE": "Anion_Pulse-Crystals.png",
    "CARRIERLAUNCHSPEEDUPGRADE": "Graviton_catapult.gif",
    "DARKTEMPLARALASADIR": "Shadow_Stride.png",
    # Zerg ground armor
    "ZERGGROUNDARMORSLEVEL1": "Ground_carapace_1.gif",
    "ZERGGROUNDARMORSLEVEL2": "Ground_carapace_2.gif",
    "ZERGGROUNDARMORSLEVEL3": "Ground_carapace_3.gif",
    "ZERGMELEEWEAPONSLEVEL1": "Melee_attacks_1.gif",
    "ZERGMELEEWEAPONSLEVEL2": "Melee_attacks_2.gif",
    "ZERGMELEEWEAPONSLEVEL3": "Melee_attacks_3.gif",
    "ZERGMISSILEWEAPONSLEVEL1": "Missile_attacks_1.gif",
    "ZERGMISSILEWEAPONSLEVEL2": "Missile_attacks_2.gif",
    "ZERGMISSILEWEAPONSLEVEL3": "Missile_attacks_3.gif",
    "ZERGFLYERATTACKLEVEL1": "Flyer_attack_1.gif",
    "ZERGFLYERATTACKLEVEL2": "Flyer_attack_2.gif",
    "ZERGFLYERATTACKLEVEL3": "Flyer_attack_3.gif",
    "ZERGFLYERARMORSLEVEL1": "Flyer_carapace_1.gif",
    "ZERGFLYERARMORSLEVEL2": "Flyer_carapace_2.gif",
    "ZERGFLYERARMORSLEVEL3": "Flyer_carapace_3.gif",
    # Zerg units
    "ZERGLINGATTACKSPEED": "Adrenal_glands.gif",
    "ZERGLINGMOVEMENTSPEED": "Metabolic_boost.gif",
    "BANELINGMOVEMENTSPEED": "Centrifugal_hooks.gif",
    "TUNNELINGCLAWS": "Tunneling_claws.gif",
    "GLIALRECONSTITUTION": "Glial_reconstitution.gif",
    "CENTRIFICALHOOKS": "Centrifugal_hooks.gif",
    "EVOLVEGROOVEDSPINES": "Grooved_Spines_LotV.png",
    "EVOLVEMUSCULARAUGMENTS": "Muscular_Augments.png",
    "LURKERRANGE": "Seismic_Spines_(Kerrigan_coop).png",
    "CHITINOUSPLATING": "Chitinous_Plating.gif",
    "ANABOLICSYNTHESIS": "Anabolic_Synthesis.gif",
    "OVERLORDSPEED": "Pneumatized_carapace.gif",
    "BURROW": "Burrow.gif",
    "NEURALPARASITE": "Neural_parasite.png",
    # Terran armor
    "TERRANINFANTRYWEAPONSLEVEL1": "Infantry_weapons_1.gif",
    "TERRANINFANTRYWEAPONSLEVEL2": "Infantry_weapons_2.gif",
    "TERRANINFANTRYWEAPONSLEVEL3": "Infantry_weapons_3.gif",
    "TERRANINFANTRYARMORSLEVEL1": "Infantry_armor_1.gif",
    "TERRANINFANTRYARMORSLEVEL2": "Infantry_armor_2.gif",
    "TERRANINFANTRYARMORSLEVEL3": "Infantry_armor_3.gif",
    "TERRANVEHICLEWEAPONSLEVEL1": "Vehicle_weapons_1.gif",
    "TERRANVEHICLEWEAPONSLEVEL2": "Vehicle_weapons_2.gif",
    "TERRANVEHICLEWEAPONSLEVEL3": "Vehicle_weapons_3.gif",
    "TERRANVEHICLEANDSHIPARMORSLEVEL1": "Vehicle_plating_1.gif",
    "TERRANVEHICLEANDSHIPARMORSLEVEL2": "Vehicle_plating_2.gif",
    "TERRANVEHICLEANDSHIPARMORSLEVEL3": "Vehicle_plating_3.gif",
    "TERRANSHIPWEAPONSLEVEL1": "Ship_weapons_1.gif",
    "TERRANSHIPWEAPONSLEVEL2": "Ship_weapons_2.gif",
    "TERRANSHIPWEAPONSLEVEL3": "Ship_weapons_3.gif",
    # Terran units
    "STIMPACK": "Stimpack.png",
    "COMBATSHIELD": "Combat_Shield.png",
    "PUNISHERGRENADES": "Concussive_Shells.png",  # Concussive Shells = Marauder upgrade
    "SIEGETECH": "Siege_mode.gif",
    "HIGHCAPACITYBARRELS": "Infernal_preigniter.jpg",  # Hellion -> Hellbat transform
    "DRILLCLAWS": "DrillingClaws.png",
    "BATTLECRUISERENABLESPECIALIZATIONS": "Behemoth_reactor.gif",
    "MEDIVACINCREASESPEEDBOOST": "Caduceus_reactor.gif",
    "NEOSTEELFRAME": "Neosteel_frames.gif",
    "HISECAUTOTRACKING": "Hisec_auto_tracking.gif",
    "BANSHEESPEED": "Hyperflight_Rotors_(Patch_3.1.2).jpg",
    "BANSHEECLOAK": "Cloak.png",
    "GHOSTCLOAK": "Cloak.png",  # same cloak icon
    "TACNUKESILO": "Yamato_cannon.png",  # best available; no nuke-specific img
    "LIBERATORAGRANGEUPGRADE": "Advanced_Ballistics_(Patch_3.1.2).jpg",
}

# ---------------------------------------------------------------------------
# MediaWiki API helpers
# ---------------------------------------------------------------------------


def api_get_image_url(filename: str) -> str | None:
    """Query Liquipedia MediaWiki API for the real CDN URL of a wiki file."""
    params = {
        "action": "query",
        "titles": "File:" + filename,
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    }
    try:
        r = SESSION.get(LIQUIPEDIA_API, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        for page in data.get("query", {}).get("pages", {}).values():
            ii = page.get("imageinfo", [])
            if ii:
                return ii[0]["url"]
    except Exception as e:
        print("  API error for " + filename + ": " + str(e), file=sys.stderr)
    return None


def download_file(url: str, dest: Path) -> bool:
    """Download url to dest. Returns True if size >= 1KB."""
    try:
        r = SESSION.get(url, timeout=20, stream=True)
        if r.status_code != 200:
            return False
        data = r.content
        if len(data) < 1024:
            return False
        dest.write_bytes(data)
        return True
    except Exception as e:
        print("  download error: " + str(e), file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def process_group(
    filename_map: dict[str, str],
    dest_dir: Path,
    sub: str,
) -> tuple[dict[str, str], list[str]]:
    """
    Returns:
      ok_map: key -> local web path e.g. /icons/sc2/buildings/NEXUS.jpg
      missing: keys that failed
    """
    ok_map: dict[str, str] = {}
    missing: list[str] = []
    total = len(filename_map)

    # Cache: liquipedia_filename -> (real_url, local_path_for_key)
    # When two keys share the same filename (e.g. GHOSTCLOAK + BANSHEECLOAK -> Cloak.png)
    # download once, map both keys to same file — but use different local filenames per key.
    url_cache: dict[str, str | None] = {}  # filename -> real_url

    for i, (key, liq_filename) in enumerate(filename_map.items(), 1):
        print(f"[{sub}] {i}/{total} {key} ({liq_filename}) ...", end=" ", flush=True)

        # Get real CDN URL (cached per liq_filename)
        if liq_filename not in url_cache:
            url_cache[liq_filename] = api_get_image_url(liq_filename)
            time.sleep(0.3)

        real_url = url_cache[liq_filename]
        if not real_url:
            print("MISS (not found in wiki)")
            missing.append(key)
            continue

        # Determine local extension from real URL
        url_path = real_url.split("?")[0]
        ext = url_path.rsplit(".", 1)[-1].lower() if "." in url_path.rsplit("/", 1)[-1] else "png"
        local_filename = key + "." + ext
        dest = dest_dir / local_filename
        local_web_path = "/icons/sc2/" + sub + "/" + local_filename

        if dest.exists() and dest.stat().st_size >= 1024:
            print("(exists) -> " + local_web_path)
            ok_map[key] = local_web_path
        else:
            success = download_file(real_url, dest)
            if success:
                size_kb = dest.stat().st_size // 1024
                print("OK (" + str(size_kb) + "KB) -> " + local_web_path)
                ok_map[key] = local_web_path
            else:
                print("DOWNLOAD FAIL (" + real_url + ")")
                missing.append(key)

    return ok_map, missing


def main() -> None:
    print("=" * 60)
    print("Downloading BUILDING icons ...")
    print("=" * 60)
    building_ok, building_miss = process_group(BUILDING_FILENAMES, BUILDINGS_DIR, "buildings")

    print()
    print("=" * 60)
    print("Downloading UPGRADE icons ...")
    print("=" * 60)
    upgrade_ok, upgrade_miss = process_group(UPGRADE_FILENAMES, UPGRADES_DIR, "upgrades")

    print()
    print("=" * 60)
    print("Downloading UNIT icons ...")
    print("=" * 60)
    unit_ok, unit_miss = process_group(UNIT_FILENAMES, UNITS_DIR, "units")

    all_missing = (
        ["[building] " + k for k in building_miss]
        + ["[upgrade] " + k for k in upgrade_miss]
        + ["[unit] " + k for k in unit_miss]
    )

    print()
    print("=" * 60)
    print("Buildings: " + str(len(building_ok)) + "/" + str(len(BUILDING_FILENAMES)) + " OK")
    print("Upgrades:  " + str(len(upgrade_ok)) + "/" + str(len(UPGRADE_FILENAMES)) + " OK")
    print("Units:     " + str(len(unit_ok)) + "/" + str(len(UNIT_FILENAMES)) + " OK")
    print("Missing:   " + str(len(all_missing)))

    if all_missing:
        MISSING_TXT.write_text("\n".join(all_missing) + "\n", encoding="utf-8")
        print("Written: " + str(MISSING_TXT))
        for m in all_missing:
            print("  MISS " + m)
    else:
        if MISSING_TXT.exists():
            MISSING_TXT.unlink()
        print("All icons downloaded!")

    result = {"buildings": building_ok, "upgrades": upgrade_ok, "units": unit_ok}
    out = BASE / "scripts" / "_icon_resolved.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nResolved map -> " + str(out))

    _sync_to_static()


def _sync_to_static() -> None:
    """把 web/public/icons/sc2 同步到 server 的 static 目录。

    static/ 是 vite 的 outDir —— 平时前端 `npm run build` 会把 public/ 拷过去。但图标不入库
    (暴雪版权美术，见 THIRD_PARTY_NOTICES)，而后端可以在**完全不装 node 的情况下**跑起来，
    所以这里直接拷一份过去：clone 完只跑本脚本就有图标，不必为了图标去装前端工具链。
    """
    import shutil

    src = BASE / "web" / "public" / "icons" / "sc2"
    dst = BASE / "src" / "vibecraft" / "server" / "static" / "icons" / "sc2"
    if not src.exists():
        print("No icons to sync (source dir missing)")
        return
    n = 0
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        target = dst / f.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        n += 1
    print("Synced " + str(n) + " icons -> " + str(dst))


if __name__ == "__main__":
    main()
