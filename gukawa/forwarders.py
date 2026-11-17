"""Japan -> Korea package forwarders (배송대행지) with their own published rate tables.

Rates are the basic member grade, read from each forwarder's official rate page on CHECKED.
Weights between listed points are interpolated, and the result is marked as an estimate.
Total cost = the shop's domestic shipping to the forwarder's warehouse + the international fee,
because warehouses sit in different regions (Osaka, Fukuoka, Tokyo) and the shop charges by region.
"""
from dataclasses import dataclass
import math

from .shop import REGIONS, SHOPS

CHECKED = "2026-11-16"
MAX_WEIGHT = 10.0
WEIGHTS = (0.5, 1, 1.5, 2, 3, 4, 5, 10)


@dataclass(frozen=True)
class Method:
    key: str
    label: str
    kind: str                  # air | sea | other
    rates: tuple               # prices at WEIGHTS
    prefectures: tuple         # warehouse(s) this method ships from


@dataclass(frozen=True)
class Forwarder:
    key: str
    name: str
    url: str
    rate_url: str
    currency: str              # JPY | KRW
    grade: str
    methods: tuple
    notes: tuple
    applied_rate: float | None = None   # the forwarder's own won-per-yen rate when it publishes one
    warehouses: str = ""


FORWARDERS = (
    Forwarder(
        "araku", "아라쿠", "https://www.araku.co.kr/",
        "https://www.araku.co.kr/Front/Page/DlvrMny_S.asp?gMnu1=203&gMnu2=20310", "JPY", "일반회원",
        (Method("air", "항공", "air", (650, 750, 842, 953, 1158, 1368, 1788, 2788), ("大阪府",)),
         Method("sea", "해운", "sea", (590, 690, 750, 875, 1066, 1262, 1406, 2306), ("大阪府",))),
        ("0.5kg 단위로 올려서 계산 (1.36kg → 1.5kg)",
         "20kg 초과, 세 변 합 140cm 초과, 한 변 100cm 이상은 화물택배로 추가 요금",
         "검수·묶음·보관 요금은 요금표 페이지에 없어요. 가입 전에 부가서비스 안내를 확인하세요",
         "일반통관 시 통관 수수료 3,300원"),
        applied_rate=8.80, warehouses="오사카"),
    Forwarder(
        "tabaejapan", "타배Japan", "https://www.tabaejapan.co.kr/",
        "https://www.tabaejapan.co.kr/Front/Page/DlvrMny_S.asp?gMnu1=203&gMnu2=20310", "JPY", "개인회원",
        (Method("air", "항공", "air", (820, 1010, 1200, 1390, 1770, 2150, 2530, 4430), ("大阪府",)),
         Method("sea", "해운", "sea", (680, 810, 940, 1070, 1330, 1590, 1850, 3150), ("大阪府",))),
        ("0.5kg 단위로 올려서 계산",
         "검수·묶음·보관 요금은 요금표 페이지에 없어요. 가입 전에 확인하세요",
         "일반통관 시 통관 수수료 3,300원"),
        warehouses="오사카"),
    Forwarder(
        "ohmyzip", "오마이집", "https://www.ohmyzip.com/",
        "https://www.ohmyzip.com/how-it-works/shipping-fee/price-chart/?ship_from=JP&ship_to=KR", "JPY", "이코노미 (이용 30회 미만)",
        (Method("fast", "빠른 배송 (2~3일)", "other", (530, 600, 690, 770, 960, 1130, 1550, 2190), ("福岡県",)),
         Method("slow", "일반 배송 (4~5일)", "other", (430, 510, 560, 630, 780, 910, 1040, 1720), ("福岡県",))),
        ("요금표가 항공/해운이 아니라 배송 일수로 나뉘어 있어요",
         "부피무게(가로×세로×높이÷6000)와 실무게 중 큰 쪽으로 계산. 일본센터 부피무게 면제 이벤트가 있을 때가 있어요",
         "기본검수 무료, 정밀검수 개당 $0.20, 30일 무료 보관, 상자 단위 묶음배송 가능",
         "요금표에 유류할증료가 포함됐는지 분명하지 않고, 홈페이지 배너의 1kg ¥960과 요금표(¥600)가 달라요. 결제 전 확인하세요",
         "이용 횟수가 늘면 등급이 올라 더 싸져요"),
        warehouses="후쿠오카"),
    Forwarder(
        "eldex", "엘덱스", "https://www.eldex.co.kr/",
        "https://www.eldex.co.kr/agency/delivery-price/deliveryCenterCode/JP", "KRW", "일반회원",
        (Method("air", "항공", "air", (9500, 11300, 13100, 14900, 18500, 22100, 25700, 43700), ("東京都", "福岡県")),
         Method("sea", "해운", "sea", (8500, 9700, 10900, 12100, 14500, 16900, 19300, 31300), ("東京都", "福岡県"))),
        ("원화 고정 요금이라 환율에 흔들리지 않아요",
         "묶음배송 송장 3개까지 무료, 4개째 2,000원 + 이후 개당 1,000원",
         "기본검수 무료, 정밀검수 개당 2,500원, 재포장 1,500원",
         "30일 무료 보관, 이후 5,000원 + 하루 1,000원",
         "도쿄·후쿠오카 창고 중 어느 쪽이 항공/해운인지 요금 페이지에 없어요",
         "이용 횟수에 따라 3~5% 할인"),
        warehouses="도쿄 · 후쿠오카"),
    Forwarder(
        "kurologi", "쿠로로지", "https://kurologi.jp/", "https://kurologi.jp/help/weightchart2.asp", "JPY", "베이직 (월 이용료 없음)",
        (Method("air", "항공 (도쿄)", "air", (720, 820, 940, 1050, 1280, 1540, 2060, 3090), ("東京都",)),),
        ("0.5kg 단위로 올려서 계산, 항공은 부피무게(÷6000) 적용",
         "합배송 3종까지 무료, 추가 종류당 ¥50 / 기본검수 무료, 사진검수 개당 ¥330 / 재포장 ¥80~",
         "30일 무료 보관, 이후 크기별 하루 ¥20~",
         "해운(후쿠오카) 요금표는 자바스크립트로만 나와 확인하지 못했어요 (0.5kg ¥680)",
         "유료 등급(월 ¥4,400~)은 요금과 부피무게 조건이 더 좋아요"),
        warehouses="도쿄 (해운은 후쿠오카)"),
    Forwarder(
        "jimpass", "짐패스", "https://jimpass.com/", "https://jimpass.com/agency/delivery-price/agencyCode/600", "JPY", "브론즈",
        (Method("air", "항공 (오사카)", "air", (1150, 1450, 1750, 2050, 2650, 3250, 3850, 6850), ("大阪府",)),
         Method("sea", "선박 (후쿠오카)", "sea", (880, 1100, 1320, 1540, 1980, 2420, 2860, 5060), ("福岡県",))),
        ("주문 때 사서함번호를 꼭 넣어야 입고가 늦어지지 않아요",
         "이용 횟수에 따라 2~10% 할인",
         "2026-11-09에 도쿄 항공센터 요금 인상 공지가 있었어요. 이 표는 오사카 항공·후쿠오카 선박 기준이에요",
         "검수·보관 요금은 요금표 페이지에 없어요"),
        applied_rate=8.67, warehouses="오사카 (항공) · 후쿠오카 (선박)"),
)

FORWARDER_MAP = {forwarder.key: forwarder for forwarder in FORWARDERS}

# Not read from official tables; pointers only.
OTHER_FORWARDERS = (
    ("몰테일·아이포터 등 다국가 배대지", "https://jimscanner.co.kr/compare/jp",
     "여러 나라 창고를 운영하는 큰 업체들. 짐스캐너 비교(2026-11-15)에서 일본 1kg 기준 2만 원대로 위 업체들보다 비싼 편이었어요."),
    ("짐스캐너 일본 배대지 비교", "https://jimscanner.co.kr/compare/jp",
     "17곳 이상의 요금을 모아 둔 제3자 비교 사이트. 다른 업체가 궁금하면 여기서 찾아보세요."),
)


def region_of(prefecture: str) -> str:
    for region, names in REGIONS.items():
        if prefecture in names:
            return region
    return ""


def price_at(rates, weight: float) -> tuple[float | None, bool]:
    """Price for a weight rounded up to 0.5kg. Returns (price, estimated?)."""
    billed = math.ceil(max(weight, 0.5) * 2) / 2
    if billed > MAX_WEIGHT:
        return None, False
    points = list(zip(WEIGHTS, rates))
    for w, p in points:
        if w == billed:
            return p, False
    for (low, low_price), (high, high_price) in zip(points, points[1:]):
        if low < billed < high:
            return low_price + (high_price - low_price) * (billed - low) / (high - low), True
    return None, False


def domestic_fee(shop_key: str, subtotal: int, prefectures: tuple) -> tuple[int | None, str]:
    """The shop's shipping to the forwarder's warehouse; the highest when a forwarder has several."""
    shop = SHOPS.get(shop_key)
    if not shop:
        return None, ""
    if shop["free_over"] and subtotal >= shop["free_over"]:
        return 0, "무료"
    if not shop["shipping"]:
        return None, "결제 화면에서 확인"
    fees = [(shop["shipping"][region_of(p)], region_of(p)) for p in prefectures if region_of(p) in shop["shipping"]]
    if not fees:
        return None, ""
    fee, region = max(fees)
    return fee, region if len(fees) == 1 else f"{region} (창고에 따라 다름)"


def compare(weight: float, *, kind: str = "", shop_key: str = "chiikawamarket", subtotal: int = 0,
            won_per_yen: float | None = None) -> list[dict]:
    """Every forwarder method with its cost for this parcel, cheapest total first."""
    rows = []
    for forwarder in FORWARDERS:
        for method in forwarder.methods:
            if kind and method.kind != kind:
                continue
            price, estimated = price_at(method.rates, weight)
            if price is None:
                continue
            fee, region = domestic_fee(shop_key, subtotal, method.prefectures)
            if forwarder.currency == "KRW":
                international_won = price
            else:
                yen_rate = forwarder.applied_rate or won_per_yen
                international_won = price * yen_rate if yen_rate else None
            if fee == 0:
                domestic_won = 0
            elif fee is not None and won_per_yen:
                domestic_won = fee * won_per_yen
            else:
                domestic_won = None
            total = international_won + (domestic_won or 0) if international_won is not None else None
            rows.append({
                "forwarder": forwarder, "method": method, "price": round(price), "estimated": estimated,
                "international_won": round(international_won) if international_won is not None else None,
                "domestic_yen": fee, "domestic_region": region,
                "total_won": round(total) if total is not None else None,
                "rate_note": "원화 요금" if forwarder.currency == "KRW" else ("업체 적용환율" if forwarder.applied_rate else "시장 환율로 환산"),
            })
    rows.sort(key=lambda row: (row["total_won"] is None, row["total_won"] or 0))
    return rows


def client_data() -> dict:
    """What the order page needs to estimate international shipping with the chosen forwarder."""
    return {"weights": WEIGHTS, "forwarders": {forwarder.key: {
        "name": forwarder.name, "currency": forwarder.currency, "applied_rate": forwarder.applied_rate,
        "methods": {method.key: {"label": method.label, "rates": method.rates, "prefectures": method.prefectures}
                    for method in forwarder.methods},
    } for forwarder in FORWARDERS}}
