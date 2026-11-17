"""Purchase guides for the official Chiikawa shops, written for buyers in Korea.

Shop facts come from each shop's ご利用ガイド (checked 2026-11-16). They change, so every guide
links the original page and shows when it was checked.
"""

CHECKED = "2026-11-16"

# Japanese Shopify checkout (both official markets and Mogumogu run on it), in on-screen order.
SHOPIFY_FIELDS = (
    ("country", "国/地域", "日本 선택"),
    ("last_name", "姓", "성"),
    ("first_name", "名", "이름"),
    ("postal", "郵便番号", "우편번호 (예: 123-4567)"),
    ("prefecture", "都道府県", "도·도·부·현 선택"),
    ("city", "市区町村", "시·구·정·촌"),
    ("street", "番地", "번지"),
    ("building", "建物名、部屋番号など", "건물명·호수"),
    ("phone", "電話番号", "전화번호"),
)

PROFILE_FIELDS = (
    ("last_name", "姓 (성)", "배대지가 안내한 형식 그대로 (예: KIM 또는 キム)"),
    ("first_name", "名 (이름)", "회원코드를 이름 뒤에 붙이라는 배대지도 있어요"),
    ("postal", "郵便番号", "123-4567"),
    ("prefecture", "都道府県", "예: 千葉県"),
    ("city", "市区町村", "예: 市川市"),
    ("street", "番地", "예: 1-2-3"),
    ("building", "建物名・部屋番号", "사서함·회원코드가 들어가는 곳인 경우가 많아요"),
    ("phone", "電話番号", "배대지 일본 전화번호"),
    ("customs_code", "개인통관고유부호", "P로 시작하는 13자리 (배대지 신청서용)"),
)

# Yamato regions as the official markets price them (¥, tax included, orders under ¥11,000).
REGIONS = {
    "北海道": ("北海道",),
    "東北": ("青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"),
    "関東": ("茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", "山梨県"),
    "中部": ("新潟県", "長野県", "富山県", "石川県", "福井県", "岐阜県", "静岡県", "愛知県", "三重県"),
    "近畿": ("滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"),
    "中国": ("鳥取県", "島根県", "岡山県", "広島県", "山口県"),
    "四国": ("徳島県", "香川県", "愛媛県", "高知県"),
    "九州": ("福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県"),
    "沖縄": ("沖縄県",),
}
MARKET_SHIPPING = {"北海道": 1320, "東北": 1100, "関東": 990, "中部": 990, "近畿": 1045,
                   "中国": 1100, "四国": 1100, "九州": 1320, "沖縄": 4070}

MARKET_PAYMENTS = "신용카드(VISA·JCB·Master·AMEX, 일시불), Apple Pay, 라쿠텐페이, d払い, PayPay, au PAY, 後払いPaidy"

SHOPS = {
    "chiikawamarket": {
        "name": "치이카와 마켓", "ja": "ちいかわマーケット", "url": "https://chiikawamarket.jp/",
        "guide_url": "https://chiikawamarket.jp/pages/guide", "news_source": "chiikawamarket",
        "summary": "치이카와 공식 온라인 숍. 신상품·재판매가 가장 먼저 올라와요.",
        "routes": ("global-e", "forwarder"),
        "global_e_url": "https://grayparkaservice.com/pages/international_shipping",
        "shipping": MARKET_SHIPPING, "free_over": 11000,
        "facts": (
            ("회원가입", "필수 (무료). 이메일 + 비밀번호(영문·숫자 5자 이상)"),
            ("결제", MARKET_PAYMENTS),
            ("일본 내 배송", "야마토 운수 택배. 주문 금액 11,000엔(세금 포함) 이상이면 무료"),
            ("발송", "재고 상품은 주문 다음 날부터 7영업일 안, 예약 상품은 출하 시작일부터 7영업일 안. 주말·공휴일 발송 없음, 날짜 지정 불가"),
            ("주문 단위", "주문번호마다 따로 발송돼요. 여러 주문을 합쳐 주지 않아요"),
        ),
        "cautions": (
            "예약 상품과 일반 상품은 한 주문에 같이 담을 수 없어요. 따로 주문하세요.",
            "주문 후에는 취소·변경이 안 돼요. 배송지(배대지 주소)를 결제 전에 꼭 확인하세요.",
            "1회 결제당 수량 제한(상품 태그의 '2個/1会計' 등)을 넘기면 주문이 통째로 취소돼요.",
            "카트에 담아도 재고가 잡히지 않아요. 인기 상품은 결제를 끝내야 확보돼요.",
            "불량 문의는 일본 주소(배대지)에 도착한 날로부터 7일 안에 해야 해요. 배대지 검수를 꼭 신청하세요.",
            "배대지 이용 중 생긴 파손·수량 부족은 쇼핑몰이 책임지지 않는다고 명시돼 있어요.",
            "인형·마스코트는 쇼핑몰이 정한 '양품 기준'이 있어요. 주문 전에 쇼핑몰의 기준 페이지를 확인하세요.",
        ),
    },
    "naganomarket": {
        "name": "나가노 마켓", "ja": "ナガノマーケット", "url": "https://nagano-market.jp/",
        "guide_url": "https://nagano-market.jp/pages/guide", "news_source": "naganomarket",
        "summary": "작가 나가노의 다른 캐릭터(농담곰 등)와 치이카와 굿즈를 파는 공식 숍. 치이카와 마켓과 같은 회사가 운영해요.",
        "routes": ("global-e", "forwarder"),
        "global_e_url": "https://grayparkaservice.com/pages/international_shipping",
        "shipping": MARKET_SHIPPING, "free_over": 11000,
        "facts": (
            ("회원가입", "필수 (무료)"),
            ("결제", MARKET_PAYMENTS),
            ("일본 내 배송", "야마토 운수 택배. 11,000엔(세금 포함) 이상 무료. 도착 희망일 지정 가능"),
            ("발송", "재고 상품은 주문 다음 날부터 7영업일 안. 주말·공휴일 발송 없음"),
            ("주문 단위", "주문번호마다 따로 발송"),
        ),
        "cautions": (
            "치이카와 마켓과 계정이 따로예요. 각각 가입해야 해요.",
            "예약 상품과 일반 상품은 따로 주문하세요.",
            "주문 후 취소·변경 불가. 수량 제한을 넘기면 주문이 취소돼요.",
            "불량 문의는 일본 주소 도착 후 7일 안. 배대지 검수를 신청하세요.",
        ),
    },
    "mogumogu": {
        "name": "치이카와 모구모구 본포", "ja": "ちいかわもぐもぐ本舗", "url": "https://chiikawamogumogu.shop/",
        "guide_url": "https://chiikawamogumogu.shop/pages/guide", "news_source": "mogumogu",
        "summary": "과자·식품과 먹거리 굿즈 중심의 공식 온라인 숍.",
        "routes": ("forwarder",),
        "shipping": None, "free_over": 11000,
        "facts": (
            ("결제", "신용카드, PayPay, LINE Pay, メルペイ, d払い, au PAY, 라쿠텐페이, 後払いPaidy"),
            ("일본 내 배송", "주문 11,000엔(세금 포함) 이상 무료(오키나와 제외). 그 이하 배송비는 결제 화면에서 확인"),
            ("발송", "결제 확인 후 보통 7~10영업일. 수주 상품은 상품 페이지 기준"),
            ("주문 단위", "주문마다 따로 발송, 합배송 없음"),
        ),
        "cautions": (
            "식품은 한국 통관 시 자가사용 기준(종류·수량)을 넘으면 막힐 수 있어요. 육류 성분이 든 식품은 반입 금지예요.",
            "유통기한이 짧은 식품은 배대지 보관·묶음 대기 기간을 고려하세요.",
            "주문 확정 후 취소·변경 불가.",
        ),
    },
}

OTHER_SHOPS = (
    ("온라인 쿠지 (치이카와 마켓)", "https://online-kuji.chiikawamarket.jp/store/", "추첨형 상품. 결제·배송 방식은 일반 마켓과 다를 수 있으니 이용 안내를 먼저 확인하세요."),
    ("치이카와 인포 (공식 종합 정보)", "https://chiikawa-info.jp/", "팝업스토어·콜라보·상설점 소식 모음. 이 사이트는 자동 수집을 막아 두어서 직접 들러 확인해야 해요."),
)

FORWARDER_STEPS = (
    ("배대지 고르기·가입", "'배대지 비교'에서 무게와 살 곳에 맞는 배대지를 고르고 가입한 뒤 '내 일본 주소'(회원코드·사서함 번호 포함)를 확인해요. "
                   "아래 '내 배대지 주소'에 한 번 저장해 두면 쇼핑몰 주소 입력 때 칸별로 복사할 수 있어요."),
    ("쇼핑몰 가입·주문", "쇼핑몰 배송지에 배대지 주소를 배대지가 안내한 형식 그대로 넣어요. 전화번호도 배대지 번호. "
                       "결제가 끝나면 주문번호와 주문 확인 메일을 보관해요."),
    ("배송신청서 미리 작성", "물건이 배대지에 도착하기 전에 신청서를 써 두면 입고가 빨라요. "
                          "쇼핑몰 이름, 주문번호, 상품 영문명·단가(엔)·수량·URL, 수취인 실명, 개인통관고유부호, 휴대폰 번호가 필요해요. "
                          "'주문 정리' 화면에서 신청서용 목록을 만들어 복사할 수 있어요."),
    ("송장번호 입력", "쇼핑몰에서 '発送のご案内' 메일이 오면 야마토 운수 송장번호(お問い合わせ番号)를 신청서에 넣어요."),
    ("입고·검수", "배대지 도착 후 검수(사진·불량 확인)를 신청해요. 쇼핑몰 불량 문의 기한이 도착 후 7일이라 빨리 확인해야 해요. "
                "여러 주문은 묶음 배송으로 합치면 국제 배송비가 줄어요."),
    ("배송비 결제·통관", "국제 배송비를 결제하면 출고돼요. 한국 도착 후 관세청 UNI-PASS에서 통관 진행을 조회할 수 있어요."),
)

CUSTOMS_NOTES = (
    "일본에서 오는 개인 자가사용 물품은 물품가격이 미화 150달러 이하면 관세·부가세가 면제돼요(미국발은 200달러).",
    "물품가격은 상품 가격(일본 소비세 포함)과 일본 내 배송비를 더한 금액이고, 국제 배송비는 빼고 계산해요.",
    "같은 날 한국에 도착(입항)하는 물건은 합쳐서 면세 여부를 판단할 수 있어요. 여러 묶음을 보낼 때는 출고 날짜를 나누세요.",
    "개인통관고유부호의 이름·휴대폰 번호가 신청서의 수취인 정보와 일치해야 해요.",
    "배터리가 든 굿즈(움직이는 인형, 소리 나는 상품 등)는 항공 배송이 제한될 수 있어요. 배대지 규정을 확인하세요.",
    "같은 상품을 여러 개 사면 판매용으로 의심받을 수 있어요. 자가사용 수량으로 사세요.",
)

ROUTES = {
    "global-e": {
        "title": "해외 직배송 (Global-e)",
        "body": "쇼핑몰 화면 왼쪽 아래에서 국가/지역을 대한민국으로 바꾸면 Global-e가 판매자가 되어 DHL로 한국에 바로 보내요(목록에 없으면 그때는 배송 불가). "
                "한국 카드로 결제하기 쉽고 신청서를 쓸 필요가 없지만, 배송비는 결제 화면에서 계산되고 주문마다 따로 붙어요. "
                "관세·세금이 결제 금액에 포함되는지 결제 화면에서 확인하세요.",
        "good": "한두 개만 살 때, 빨리 받고 싶을 때",
    },
    "forwarder": {
        "title": "배송대행지(배대지)",
        "body": "일본 안의 배대지 주소로 주문하고, 배대지가 모아서 한국으로 보내요. 여러 쇼핑몰·여러 주문을 한 상자로 묶을 수 있어요. "
                "일본 쇼핑몰에서 한국 발행 카드가 거절되면 Apple Pay나 다른 카드로 다시 시도해 보세요.",
        "good": "여러 개·여러 쇼핑몰을 묶어 살 때",
    },
}

# Japanese keywords -> English item name for the forwarder's declaration form.
CATEGORIES = (
    ("ぬいぐるみ", "Plush toy"), ("マスコット", "Plush mascot keychain"), ("キーホルダー", "Keychain"),
    ("キーチェーン", "Keychain"), ("アクリル", "Acrylic stand"), ("缶バッジ", "Can badge"), ("バッジ", "Badge"),
    ("巾着", "Drawstring pouch"), ("ポーチ", "Pouch"), ("トート", "Tote bag"), ("バッグ", "Bag"),
    ("ステッカー", "Sticker"), ("シール", "Sticker"), ("ノート", "Notebook"), ("ペン", "Pen"),
    ("メモ", "Memo pad"), ("Tシャツ", "T-shirt"), ("タオル", "Towel"), ("マグ", "Mug"), ("グラス", "Glass"),
    ("皿", "Plate"), ("ケース", "Case"), ("フィギュア", "Figure"), ("クッキー", "Cookies (snack)"),
    ("チョコ", "Chocolate (snack)"), ("せんべい", "Rice crackers (snack)"), ("キャンディ", "Candy (snack)"),
    ("グミ", "Gummy (snack)"), ("ふりかけ", "Rice seasoning (food)"), ("カード", "Card"), ("くじ", "Lottery goods"),
)


def shop_for_source(source_key: str) -> str:
    for key, shop in SHOPS.items():
        if shop["news_source"] == source_key or source_key == f"{shop['news_source']}-notice":
            return key
    return ""
