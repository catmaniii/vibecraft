"""名称本地化（i18n 接口预埋，2026-06-03 用户）。

把"内置英文 id → 显示名"的映射从 director 抽出，按 **locale** 索引。当前只实装
`zh`（中文）。将来加语言：在对应 `*_NAMES` 表里加一列 locale 即可，`Localizer`
和所有调用方代码不变 —— 这就是预埋的多语言接口。

设计：
- 表结构 `{locale: {key: 显示名}}`。查不到该 locale → 回退 DEFAULT_LOCALE →
  再回退原 key（英文 id）。回退到 id 不崩，且一眼看出缺哪条翻译。
- `Localizer(locale)` 是唯一入口。director 持一个实例 `self._loc`，locale 未来
  可由 server config / 客户端 Accept-Language 注入（现在固定 zh）。
- director 仍保留 `_UNIT_ZH` 等 ClassVar，但**别名指向这里的 zh 表**（单一数据
  源，零改动现有 `.get()` 调用点）。

key 大小写约定（沿用 director 历史）：
- 单位：PascalCase（"Zealot"），与 selector.unit_type / aliases 对齐
- 升级 / 建筑：UPPERCASE（UnitTypeId.name / UpgradeId.name）
- 动词（战术 verb）：lowercase（"attack"）
"""

from __future__ import annotations

DEFAULT_LOCALE = "zh"

# 单位名（PascalCase key）
UNIT_NAMES: dict[str, dict[str, str]] = {
    "zh": {
        "Probe": "探机",
        "Zealot": "叉子",
        "Stalker": "追猎",
        "Sentry": "哨兵",
        "Adept": "使徒",
        "HighTemplar": "HT",
        "DarkTemplar": "DT",
        "Archon": "白球",
        "Immortal": "不朽",
        "Observer": "OB",
        "WarpPrism": "棱镜",
        "Colossus": "巨像",
        "Disruptor": "干扰者",
        "Phoenix": "凤凰",
        "VoidRay": "虚空",
        "Oracle": "先知",
        "Tempest": "风暴战舰",
        "Carrier": "航母",
        "Mothership": "母舰",
    },
    "en": {
        "Probe": "Probe",
        "Zealot": "Zealot",
        "Stalker": "Stalker",
        "Sentry": "Sentry",
        "Adept": "Adept",
        "HighTemplar": "High Templar",
        "DarkTemplar": "Dark Templar",
        "Archon": "Archon",
        "Immortal": "Immortal",
        "Observer": "Observer",
        "WarpPrism": "Warp Prism",
        "Colossus": "Colossus",
        "Disruptor": "Disruptor",
        "Phoenix": "Phoenix",
        "VoidRay": "Void Ray",
        "Oracle": "Oracle",
        "Tempest": "Tempest",
        "Carrier": "Carrier",
        "Mothership": "Mothership",
    },
}

# 兵种**官方正式名**（面板用，区别于 UNIT_NAMES 黑话表；#572 批3 评审：两套寄存器分开）。
# key = **全大写** UnitTypeId.name（对齐 snapshot 的 tid_name）；zh = 官方中文名（原 director
# _ARMY_UNIT_ZH_NAMES），en = 官方英文名。全族（神/虫/人）。snapshot 兵种行走 Localizer.army_unit()。
ARMY_UNIT_NAMES: dict[str, dict[str, str]] = {
    "zh": {
        # 神族
        "PROBE": "探机",
        "ZEALOT": "狂热者",
        "STALKER": "追猎者",
        "ADEPT": "使徒",
        "SENTRY": "哨兵",
        "HIGHTEMPLAR": "高阶圣堂武士",
        "DARKTEMPLAR": "黑暗圣堂武士",
        "ARCHON": "执政官",
        "IMMORTAL": "不朽者",
        "COLOSSUS": "巨像",
        "DISRUPTOR": "干扰者",
        "OBSERVER": "观察者",
        "WARPPRISM": "折跃棱镜",
        "PHOENIX": "凤凰",
        "VOIDRAY": "虚空辉光舰",
        "ORACLE": "先知",
        "CARRIER": "航母",
        "TEMPEST": "风暴战舰",
        "MOTHERSHIP": "母舰",
        # 虫族
        "DRONE": "工蜂",
        "QUEEN": "王虫",
        "ZERGLING": "跳虫",
        "BANELING": "爆虫",
        "ROACH": "蟑螂",
        "RAVAGER": "破坏者",
        "HYDRALISK": "刺蛇",
        "LURKERMP": "潜伏者",
        "INFESTOR": "感染虫",
        "SWARMHOSTMP": "虫群宿主",
        "ULTRALISK": "雷兽",
        "OVERSEER": "王母",
        "MUTALISK": "异龙",
        "CORRUPTOR": "腐化者",
        "BROODLORD": "巢虫领主",
        "VIPER": "飞蛇",
        # 人族
        "SCV": "SCV",
        "MARINE": "陆战队员",
        "MARAUDER": "劫掠者",
        "REAPER": "死神",
        "GHOST": "幽灵",
        "HELLION": "恶火",
        "WIDOWMINE": "寡妇雷",
        "SIEGETANK": "攻城坦克",
        "CYCLONE": "飓风",
        "THOR": "雷神",
        "VIKINGFIGHTER": "维京战机",
        "MEDIVAC": "医疗运输机",
        "LIBERATOR": "解放者",
        "RAVEN": "渡鸦",
        "BANSHEE": "女妖",
        "BATTLECRUISER": "战列巡洋舰",
    },
    "en": {
        # Protoss
        "PROBE": "Probe",
        "ZEALOT": "Zealot",
        "STALKER": "Stalker",
        "ADEPT": "Adept",
        "SENTRY": "Sentry",
        "HIGHTEMPLAR": "High Templar",
        "DARKTEMPLAR": "Dark Templar",
        "ARCHON": "Archon",
        "IMMORTAL": "Immortal",
        "COLOSSUS": "Colossus",
        "DISRUPTOR": "Disruptor",
        "OBSERVER": "Observer",
        "WARPPRISM": "Warp Prism",
        "PHOENIX": "Phoenix",
        "VOIDRAY": "Void Ray",
        "ORACLE": "Oracle",
        "CARRIER": "Carrier",
        "TEMPEST": "Tempest",
        "MOTHERSHIP": "Mothership",
        # Zerg
        "DRONE": "Drone",
        "QUEEN": "Queen",
        "ZERGLING": "Zergling",
        "BANELING": "Baneling",
        "ROACH": "Roach",
        "RAVAGER": "Ravager",
        "HYDRALISK": "Hydralisk",
        "LURKERMP": "Lurker",
        "INFESTOR": "Infestor",
        "SWARMHOSTMP": "Swarm Host",
        "ULTRALISK": "Ultralisk",
        "OVERSEER": "Overseer",
        "MUTALISK": "Mutalisk",
        "CORRUPTOR": "Corruptor",
        "BROODLORD": "Brood Lord",
        "VIPER": "Viper",
        # Terran
        "SCV": "SCV",
        "MARINE": "Marine",
        "MARAUDER": "Marauder",
        "REAPER": "Reaper",
        "GHOST": "Ghost",
        "HELLION": "Hellion",
        "WIDOWMINE": "Widow Mine",
        "SIEGETANK": "Siege Tank",
        "CYCLONE": "Cyclone",
        "THOR": "Thor",
        "VIKINGFIGHTER": "Viking",
        "MEDIVAC": "Medivac",
        "LIBERATOR": "Liberator",
        "RAVEN": "Raven",
        "BANSHEE": "Banshee",
        "BATTLECRUISER": "Battlecruiser",
    },
}

# 升级名（UPPERCASE key = UpgradeId.name）
UPGRADE_NAMES: dict[str, dict[str, str]] = {
    "zh": {
        # 神族攻防护
        "PROTOSSGROUNDWEAPONSLEVEL1": "+1攻",
        "PROTOSSGROUNDWEAPONSLEVEL2": "+2攻",
        "PROTOSSGROUNDWEAPONSLEVEL3": "+3攻",
        "PROTOSSGROUNDARMORSLEVEL1": "+1防",
        "PROTOSSGROUNDARMORSLEVEL2": "+2防",
        "PROTOSSGROUNDARMORSLEVEL3": "+3防",
        "PROTOSSSHIELDSLEVEL1": "+1盾",
        "PROTOSSSHIELDSLEVEL2": "+2盾",
        "PROTOSSSHIELDSLEVEL3": "+3盾",
        "PROTOSSAIRWEAPONSLEVEL1": "空+1攻",
        "PROTOSSAIRWEAPONSLEVEL2": "空+2攻",
        "PROTOSSAIRWEAPONSLEVEL3": "空+3攻",
        "PROTOSSAIRARMORSLEVEL1": "空+1防",
        "PROTOSSAIRARMORSLEVEL2": "空+2防",
        "PROTOSSAIRARMORSLEVEL3": "空+3防",
        # 神族兵种
        "WARPGATERESEARCH": "折跃",
        "CHARGE": "冲锋",
        "BLINKTECH": "闪现",
        "ADEPTPIERCINGATTACK": "使徒穿刺",
        "PSISTORMTECH": "风暴",
        "HALLUCINATION": "幻象",
        "OBSERVERGRAVITICBOOSTER": "OB加速",
        "GRAVITICDRIVE": "棱镜加速",
        "EXTENDEDTHERMALLANCE": "巨像射程",
        "PHOENIXRANGEUPGRADE": "凤凰射程",
        "CARRIERLAUNCHSPEEDUPGRADE": "航母加速",
        "DARKTEMPLARALASADIR": "DT技能",
        # 虫族攻防
        "ZERGGROUNDARMORSLEVEL1": "+1甲",
        "ZERGGROUNDARMORSLEVEL2": "+2甲",
        "ZERGGROUNDARMORSLEVEL3": "+3甲",
        "ZERGMELEEWEAPONSLEVEL1": "+1近",
        "ZERGMELEEWEAPONSLEVEL2": "+2近",
        "ZERGMELEEWEAPONSLEVEL3": "+3近",
        "ZERGMISSILEWEAPONSLEVEL1": "+1远",
        "ZERGMISSILEWEAPONSLEVEL2": "+2远",
        "ZERGMISSILEWEAPONSLEVEL3": "+3远",
        "ZERGFLYERATTACKLEVEL1": "飞+1攻",
        "ZERGFLYERATTACKLEVEL2": "飞+2攻",
        "ZERGFLYERATTACKLEVEL3": "飞+3攻",
        "ZERGFLYERARMORSLEVEL1": "飞+1甲",
        "ZERGFLYERARMORSLEVEL2": "飞+2甲",
        "ZERGFLYERARMORSLEVEL3": "飞+3甲",
        # 虫族兵种
        "ZERGLINGATTACKSPEED": "小狗攻速",
        "ZERGLINGMOVEMENTSPEED": "小狗加速",
        "BANELINGMOVEMENTSPEED": "妖虫滚",
        "TUNNELINGCLAWS": "蟑螂挖",
        "GLIALRECONSTITUTION": "蟑螂速",
        "CENTRIFICALHOOKS": "妖虫快",
        "EVOLVEGROOVEDSPINES": "刺蛇射程",
        "EVOLVEMUSCULARAUGMENTS": "刺蛇速",
        "LURKERRANGE": "潜伏者射程",
        "CHITINOUSPLATING": "雷兽甲",
        "ANABOLICSYNTHESIS": "雷兽速",
        "OVERLORDSPEED": "老爷机速",
        "BURROW": "挖洞",
        "NEURALPARASITE": "神经寄生",
        # 人族攻防
        "TERRANINFANTRYWEAPONSLEVEL1": "+1步攻",
        "TERRANINFANTRYWEAPONSLEVEL2": "+2步攻",
        "TERRANINFANTRYWEAPONSLEVEL3": "+3步攻",
        "TERRANINFANTRYARMORSLEVEL1": "+1步防",
        "TERRANINFANTRYARMORSLEVEL2": "+2步防",
        "TERRANINFANTRYARMORSLEVEL3": "+3步防",
        "TERRANVEHICLEWEAPONSLEVEL1": "+1机攻",
        "TERRANVEHICLEWEAPONSLEVEL2": "+2机攻",
        "TERRANVEHICLEWEAPONSLEVEL3": "+3机攻",
        "TERRANVEHICLEANDSHIPARMORSLEVEL1": "+1机防",
        "TERRANVEHICLEANDSHIPARMORSLEVEL2": "+2机防",
        "TERRANVEHICLEANDSHIPARMORSLEVEL3": "+3机防",
        "TERRANSHIPWEAPONSLEVEL1": "+1空攻",
        "TERRANSHIPWEAPONSLEVEL2": "+2空攻",
        "TERRANSHIPWEAPONSLEVEL3": "+3空攻",
        # 人族兵种
        "STIMPACK": "兴奋剂",
        "COMBATSHIELD": "盾",
        "PUNISHERGRENADES": "手雷",
        "SIEGETECH": "坦克Siege",
        "HIGHCAPACITYBARRELS": "掠夺者速",
        "DRILLCLAWS": "掠夺者挖",
        "BATTLECRUISERENABLESPECIALIZATIONS": "BC专精",
        "MEDIVACINCREASESPEEDBOOST": "医疗船加速",
        "NEOSTEELFRAME": "补给站缩",
        "HISECAUTOTRACKING": "导弹追踪",
        "BANSHEESPEED": "女妖速",
        "BANSHEECLOAK": "女妖隐",
        "GHOSTCLOAK": "幽灵隐",
        "TACNUKESILO": "核弹",
        "LIBERATORAGRANGEUPGRADE": "解放者射程",
    },
    "en": {
        # Protoss ground attack/armor/shields
        "PROTOSSGROUNDWEAPONSLEVEL1": "+1 Atk",
        "PROTOSSGROUNDWEAPONSLEVEL2": "+2 Atk",
        "PROTOSSGROUNDWEAPONSLEVEL3": "+3 Atk",
        "PROTOSSGROUNDARMORSLEVEL1": "+1 Armor",
        "PROTOSSGROUNDARMORSLEVEL2": "+2 Armor",
        "PROTOSSGROUNDARMORSLEVEL3": "+3 Armor",
        "PROTOSSSHIELDSLEVEL1": "+1 Shield",
        "PROTOSSSHIELDSLEVEL2": "+2 Shield",
        "PROTOSSSHIELDSLEVEL3": "+3 Shield",
        "PROTOSSAIRWEAPONSLEVEL1": "Air +1 Atk",
        "PROTOSSAIRWEAPONSLEVEL2": "Air +2 Atk",
        "PROTOSSAIRWEAPONSLEVEL3": "Air +3 Atk",
        "PROTOSSAIRARMORSLEVEL1": "Air +1 Armor",
        "PROTOSSAIRARMORSLEVEL2": "Air +2 Armor",
        "PROTOSSAIRARMORSLEVEL3": "Air +3 Armor",
        # Protoss units
        "WARPGATERESEARCH": "Warp Gate",
        "CHARGE": "Charge",
        "BLINKTECH": "Blink",
        "ADEPTPIERCINGATTACK": "Glaives",
        "PSISTORMTECH": "Storm",
        "HALLUCINATION": "Hallucination",
        "OBSERVERGRAVITICBOOSTER": "Obs Speed",
        "GRAVITICDRIVE": "Prism Speed",
        "EXTENDEDTHERMALLANCE": "Colossus Range",
        "PHOENIXRANGEUPGRADE": "Phoenix Range",
        "CARRIERLAUNCHSPEEDUPGRADE": "Carrier Speed",
        "DARKTEMPLARALASADIR": "DT Blink",
        # Zerg ground attack/armor
        "ZERGGROUNDARMORSLEVEL1": "+1 Armor",
        "ZERGGROUNDARMORSLEVEL2": "+2 Armor",
        "ZERGGROUNDARMORSLEVEL3": "+3 Armor",
        "ZERGMELEEWEAPONSLEVEL1": "+1 Melee",
        "ZERGMELEEWEAPONSLEVEL2": "+2 Melee",
        "ZERGMELEEWEAPONSLEVEL3": "+3 Melee",
        "ZERGMISSILEWEAPONSLEVEL1": "+1 Range",
        "ZERGMISSILEWEAPONSLEVEL2": "+2 Range",
        "ZERGMISSILEWEAPONSLEVEL3": "+3 Range",
        "ZERGFLYERATTACKLEVEL1": "Air +1 Atk",
        "ZERGFLYERATTACKLEVEL2": "Air +2 Atk",
        "ZERGFLYERATTACKLEVEL3": "Air +3 Atk",
        "ZERGFLYERARMORSLEVEL1": "Air +1 Armor",
        "ZERGFLYERARMORSLEVEL2": "Air +2 Armor",
        "ZERGFLYERARMORSLEVEL3": "Air +3 Armor",
        # Zerg units
        "ZERGLINGATTACKSPEED": "Ling Atk Spd",
        "ZERGLINGMOVEMENTSPEED": "Ling Speed",
        "BANELINGMOVEMENTSPEED": "Bane Speed",
        "TUNNELINGCLAWS": "Roach Burrow Move",
        "GLIALRECONSTITUTION": "Roach Speed",
        "CENTRIFICALHOOKS": "Bane Atk Spd",
        "EVOLVEGROOVEDSPINES": "Hydra Range",
        "EVOLVEMUSCULARAUGMENTS": "Hydra Speed",
        "LURKERRANGE": "Lurker Range",
        "CHITINOUSPLATING": "Ultra Armor",
        "ANABOLICSYNTHESIS": "Ultra Speed",
        "OVERLORDSPEED": "Ovie Speed",
        "BURROW": "Burrow",
        "NEURALPARASITE": "Neural Parasite",
        # Terran infantry/vehicle/ship attack and armor
        "TERRANINFANTRYWEAPONSLEVEL1": "+1 Inf Atk",
        "TERRANINFANTRYWEAPONSLEVEL2": "+2 Inf Atk",
        "TERRANINFANTRYWEAPONSLEVEL3": "+3 Inf Atk",
        "TERRANINFANTRYARMORSLEVEL1": "+1 Inf Armor",
        "TERRANINFANTRYARMORSLEVEL2": "+2 Inf Armor",
        "TERRANINFANTRYARMORSLEVEL3": "+3 Inf Armor",
        "TERRANVEHICLEWEAPONSLEVEL1": "+1 Veh Atk",
        "TERRANVEHICLEWEAPONSLEVEL2": "+2 Veh Atk",
        "TERRANVEHICLEWEAPONSLEVEL3": "+3 Veh Atk",
        "TERRANVEHICLEANDSHIPARMORSLEVEL1": "+1 Veh Armor",
        "TERRANVEHICLEANDSHIPARMORSLEVEL2": "+2 Veh Armor",
        "TERRANVEHICLEANDSHIPARMORSLEVEL3": "+3 Veh Armor",
        "TERRANSHIPWEAPONSLEVEL1": "+1 Ship Atk",
        "TERRANSHIPWEAPONSLEVEL2": "+2 Ship Atk",
        "TERRANSHIPWEAPONSLEVEL3": "+3 Ship Atk",
        # Terran units
        "STIMPACK": "Stim",
        "COMBATSHIELD": "Combat Shield",
        "PUNISHERGRENADES": "Conc Shells",
        "SIEGETECH": "Siege",
        "HIGHCAPACITYBARRELS": "Marauder Speed",
        "DRILLCLAWS": "Drill Claws",
        "BATTLECRUISERENABLESPECIALIZATIONS": "BC Specs",
        "MEDIVACINCREASESPEEDBOOST": "Medivac Boost",
        "NEOSTEELFRAME": "Neosteel",
        "HISECAUTOTRACKING": "Auto-Tracking",
        "BANSHEESPEED": "Banshee Speed",
        "BANSHEECLOAK": "Banshee Cloak",
        "GHOSTCLOAK": "Ghost Cloak",
        "TACNUKESILO": "Nuke",
        "LIBERATORAGRANGEUPGRADE": "Liberator Range",
    },
}

# 产能建筑名（UPPERCASE key）
PRODUCTION_BUILDING_NAMES: dict[str, dict[str, str]] = {
    "zh": {
        "NEXUS": "NX",
        "GATEWAY": "BG",
        "WARPGATE": "BG",
        "STARGATE": "VS",
        "ROBOTICSFACILITY": "VR",
        "HATCHERY": "BH",
        "LAIR": "BH(Lair)",
        "HIVE": "BH(Hive)",
        "COMMANDCENTER": "BC",
        "ORBITALCOMMAND": "BC(OC)",
        "PLANETARYFORTRESS": "行星要塞",
        "BARRACKS": "BB",
        "FACTORY": "VF",
        "STARPORT": "VS(人族)",
    },
    "en": {
        "NEXUS": "NX",
        "GATEWAY": "BG",
        "WARPGATE": "BG",
        "STARGATE": "VS",
        "ROBOTICSFACILITY": "VR",
        "HATCHERY": "BH",
        "LAIR": "BH(Lair)",
        "HIVE": "BH(Hive)",
        "COMMANDCENTER": "BC",
        "ORBITALCOMMAND": "BC(OC)",
        "PLANETARYFORTRESS": "PF",
        "BARRACKS": "BB",
        "FACTORY": "VF",
        "STARPORT": "VS(T)",
    },
}

# 科技建筑名 → 中文 hotkey（UPPERCASE key）
TECH_BUILDING_NAMES: dict[str, dict[str, str]] = {
    "zh": {
        "CYBERNETICSCORE": "BY",
        "FORGE": "BF",
        "TWILIGHTCOUNCIL": "VC",
        "ROBOTICSBAY": "VB",
        "FLEETBEACON": "VF",
        "TEMPLARARCHIVE": "VT",
        "DARKSHRINE": "VD",
        "SPAWNINGPOOL": "BS",
        "ROACHWARREN": "BR",
        "BANELINGNEST": "BB",
        "EVOLUTIONCHAMBER": "BV",
        "HYDRALISKDEN": "VH",
        "LURKERDENMP": "VD",
        "INFESTATIONPIT": "VI",
        "SPIRE": "VS",
        "GREATERSPIRE": "大刺翼",
        "ULTRALISKCAVERN": "VU",
        "NYDUSNETWORK": "VN",
        "ENGINEERINGBAY": "BE",
        "ARMORY": "VA",
        "GHOSTACADEMY": "VG",
        "FUSIONCORE": "VC",
    },
    "en": {
        "CYBERNETICSCORE": "BY",
        "FORGE": "BF",
        "TWILIGHTCOUNCIL": "VC",
        "ROBOTICSBAY": "VB",
        "FLEETBEACON": "VF",
        "TEMPLARARCHIVE": "VT",
        "DARKSHRINE": "VD",
        "SPAWNINGPOOL": "BS",
        "ROACHWARREN": "BR",
        "BANELINGNEST": "BB",
        "EVOLUTIONCHAMBER": "BV",
        "HYDRALISKDEN": "VH",
        "LURKERDENMP": "VD",
        "INFESTATIONPIT": "VI",
        "SPIRE": "VS",
        "GREATERSPIRE": "Greater Spire",
        "ULTRALISKCAVERN": "VU",
        "NYDUSNETWORK": "VN",
        "ENGINEERINGBAY": "BE",
        "ARMORY": "VA",
        "GHOSTACADEMY": "VG",
        "FUSIONCORE": "VC",
    },
}

# 种族名（lowercase key = race id）
RACE_NAMES: dict[str, dict[str, str]] = {
    "zh": {"protoss": "神族", "terran": "人族", "zerg": "虫族"},
    "en": {"protoss": "Protoss", "terran": "Terran", "zerg": "Zerg"},
}

# 战术动词（lowercase key = TacticalVerb.value）
VERB_NAMES: dict[str, dict[str, str]] = {
    "zh": {
        "attack": "进攻",
        "defend": "守",
        "scout": "探",
        "recon": "火力侦查",
        "expand": "开矿",
        "harass": "骚扰",
        "drop": "投放",
        "vision": "探视野",
        "raze": "拆",
        "retreat": "撤退",
        "regroup": "集结",
        "split": "分兵",
        "patrol": "巡逻",
        "build": "建造",
        "move": "移动",
    },
    "en": {
        "attack": "Attack",
        "defend": "Defend",
        "scout": "Scout",
        "recon": "Recon",
        "expand": "Expand",
        "harass": "Harass",
        "drop": "Drop",
        "vision": "Vision",
        "raze": "Raze",
        "retreat": "Retreat",
        "regroup": "Regroup",
        "split": "Split",
        "patrol": "Patrol",
        "build": "Build",
        "move": "Move",
    },
}


class Localizer:
    """名称本地化入口。director 持一个实例；locale 未来可注入（现固定 zh）。"""

    def __init__(self, locale: str = DEFAULT_LOCALE) -> None:
        self.locale = locale

    def _lookup(self, table: dict[str, dict[str, str]], key: str, *, upper: bool) -> str:
        if not key:
            return key
        k = key.upper() if upper else key
        loc = table.get(self.locale, {})
        fallback = table.get(DEFAULT_LOCALE, {})
        return loc.get(k) or fallback.get(k) or key

    def unit(self, name: str) -> str:
        """兵种**黑话**名（PascalCase key；指令卡用，如 叉子/追猎）→ 显示名。"""
        return self._lookup(UNIT_NAMES, name, upper=False)

    def army_unit(self, name: str) -> str:
        """兵种**官方正式名**（UPPERCASE key=UnitTypeId.name；兵种面板用，如 狂热者/追猎者）→ 显示名。

        与 `unit()`（黑话）是两套寄存器（#572 批3 评审）。key 全大写对齐 snapshot 的 tid_name。
        """
        return self._lookup(ARMY_UNIT_NAMES, name, upper=True)

    def upgrade(self, name: str) -> str:
        """升级英文名 → 显示名。"""
        return self._lookup(UPGRADE_NAMES, name, upper=True)

    def verb(self, name: str) -> str:
        """战术动词 → 显示名。"""
        return self._lookup(VERB_NAMES, name, upper=False)

    def race(self, name: str) -> str:
        """种族 id（protoss/terran/zerg）→ 本地化种族名。"""
        return self._lookup(RACE_NAMES, name, upper=False)

    def structure(self, name: str) -> str:
        """建筑英文名 → 显示名（先科技建筑表，再产能建筑表）。"""
        if not name:
            return name
        k = name.upper()
        loc = self.locale
        return (
            TECH_BUILDING_NAMES.get(loc, {}).get(k)
            or PRODUCTION_BUILDING_NAMES.get(loc, {}).get(k)
            or TECH_BUILDING_NAMES.get(DEFAULT_LOCALE, {}).get(k)
            or PRODUCTION_BUILDING_NAMES.get(DEFAULT_LOCALE, {}).get(k)
            or name
        )
