"""Simplified/Traditional Chinese helpers for .lad comments and lookups.

Syntec LadEditor stores element comments as cp950/Big5 traditional Chinese.
Writing simplified characters produces question marks on the HMI, so the SDK
needs a way to (a) detect simplified text before it is written and (b) convert
the common machine-tool vocabulary to traditional forms.

The table is intentionally small and domain-focused: it covers the characters
that actually appear in PLC comments, not the full CJK repertoire.
"""

from __future__ import annotations

# 简体专用字 -> 繁体。仅收录机床/电气/界面常用的字，避免误伤。
SIMPLE_TO_TRAD: dict[str, str] = {
    "门": "門", "轴": "軸", "报": "報", "警": "警", "润": "潤", "滑": "滑",
    "压": "壓", "关": "關", "开": "開", "设": "設", "备": "備", "产": "產",
    "业": "業", "务": "務", "动": "動", "线": "線", "图": "圖", "电": "電",
    "气": "氣", "阀": "閥", "缸": "缸", "头": "頭", "转": "轉", "换": "換",
    "刀": "刀", "库": "庫", "夹": "夾", "紧": "緊", "松": "鬆", "测": "測",
    "试": "試", "数": "數", "据": "據", "误": "誤", "差": "差", "参": "參",
    "单": "單", "复": "復", "启": "啟", "进": "進", "给": "給", "速": "速",
    "度": "度", "液": "液", "温": "溫", "却": "卻", "屑": "屑", "链": "鏈",
    "马": "馬", "达": "達", "过": "過", "载": "載", "烧": "燒", "险": "險",
    "丝": "絲", "继": "繼", "触": "觸", "源": "源", "灯": "燈", "鸣": "鳴",
    "钮": "鈕", "择": "擇", "模": "模", "态": "態", "汉": "漢", "语": "語",
    "显": "顯", "示": "示", "画": "畫", "页": "頁", "确": "確", "认": "認",
    "取": "取", "消": "消", "返": "返", "释": "釋", "态": "態", "讯": "訊",
    "号": "號", "红": "紅", "绿": "綠", "黄": "黃", "闪": "閃", "烁": "爍",
    "车": "車", "铣": "銑", "钻": "鑽", "攻": "攻", "牙": "牙", "锥": "錐",
    "丝": "絲", "杠": "槓", "轨": "軌", "链": "鏈", "板": "板", "带": "帶",
    "输": "輸", "送": "送", "传": "傳", "绕": "繞", "组": "組", "统": "統",
    "调": "調", "节": "節", "整": "整", "个": "個", "别": "別", "级": "級",
    "变": "變", "频": "頻", "器": "器", "压": "壓", "缩": "縮", "机": "機",
    "检": "檢", "维": "維", "护": "護", "养": "養", "清": "清", "洁": "潔",
    "运": "運", "行": "行", "状": "狀", "态": "態", "复": "復", "位": "位",
    "归": "歸", "零": "零", "原": "原", "点": "點", "间": "間", "隔": "隔",
    "延": "延", "迟": "遲", "续": "續", "暂": "暫", "停": "停", "断": "斷",
    "锁": "鎖", "钥": "鑰", "密": "密", "码": "碼", "权": "權", "限": "限",
    "测": "測", "量": "量", "记": "記", "录": "錄", "历": "歷", "史": "史",
    "账": "賬", "户": "戶", "删": "刪", "除": "除", "添": "添", "加": "加",
    "载": "載", "入": "入", "出": "出", "读": "讀", "写": "寫", "储": "儲",
    "存": "存", "缓": "緩", "冲": "衝", "击": "擊", "撞": "撞", "护": "護",
    "请": "請", "后": "後", "里": "裡", "见": "見", "识": "識", "为": "為",
    "远": "遠", "还": "還", "这": "這", "从": "從", "会": "會", "们": "們",
    "无": "無", "与": "與", "万": "萬", "术": "術", "极": "極", "标": "標",
    "准": "準", "验": "驗", "规": "規", "则": "則", "严": "嚴", "说": "說",
    "监": "監", "阶": "階", "顺": "順", "逻": "邏", "辑": "輯", "条": "條",
    "闭": "閉", "应": "應", "馈": "饋", "弹": "彈", "簧": "簧", "齿": "齒",
    "轮": "輪", "减": "減", "须": "須", "谓": "謂", "语": "語", "义": "義",
    "挥": "揮", "损": "損", "伤": "傷", "强": "強", "弱": "弱", "备": "備",
}

TRAD_ONLY = {v for v in SIMPLE_TO_TRAD.values() if v != ""}


def to_traditional(text: str) -> str:
    """Convert the covered simplified characters to their traditional forms."""

    return "".join(SIMPLE_TO_TRAD.get(ch, ch) for ch in text)


def to_simplified(text: str) -> str:
    """Best-effort reverse conversion using the same domain table."""

    reverse = {trad: simp for simp, trad in SIMPLE_TO_TRAD.items() if simp != trad}
    return "".join(reverse.get(ch, ch) for ch in text)


def find_simplified(text: str) -> list[dict[str, object]]:
    """Return the simplified characters present in ``text`` with their position."""

    hits: list[dict[str, object]] = []
    for index, ch in enumerate(text):
        replacement = SIMPLE_TO_TRAD.get(ch)
        if replacement and replacement != ch:
            hits.append(
                {
                    "index": index,
                    "simplified": ch,
                    "traditional": replacement,
                }
            )
    return hits


def has_simplified(text: str) -> bool:
    return any(SIMPLE_TO_TRAD.get(ch, ch) != ch for ch in text)
