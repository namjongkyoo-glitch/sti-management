"""실행품의 별첨 시트 (제작비용/직접경비/현지운영비) 항목 정의 및 입력/계산"""

# 별첨2: 직접경비 고정 항목 (양식 순서대로)
DIRECT_EXPENSE_ITEMS = [
    ("여비교통비", "국내/해외 출장 식비·일비·항공료 등"),
    ("소모품비(제조용)", "제조용 볼트, 잡자재 등"),
    ("소모품비(Set-up)", "현장 Set-up용 자재"),
    ("복리후생비", "식대, 검진비, 조직활성화비 등"),
    ("수도광열비", "도시가스, 전기, 수도료 등"),
    ("도서인쇄비", "도서구입, 제본비 등"),
    ("통신비", "전화료, 인터넷, 로밍 등"),
    ("수선비", "기계장치/PC/비품 수리"),
    ("세금과공과", "각종 세금"),
    ("보험료", "운송/이행/고용/산재 등"),
    ("지급수수료", "인증, 비자/여권, 자문료 등"),
    ("지급수수료_보증서", "계약/선급금/하자 이행증권"),
    ("지급임차료", "건물/복사기/정수기 등 임차"),
    ("운반비", "운반, 택배/퀵, 지게차, 포장비"),
    ("수입부대비", "관세, 통관, 창고료 등"),
    ("수출제비용", "통관비용, 창고료 등"),
    ("잡급", "국내 일용직 일당"),
    ("기타비용", "직접경비 중 예비비 해당"),
]

# 별첨3: 현지운영비 고정 항목 (양식 순서대로)
LOCAL_OPS_ITEMS = [
    ("현지운영비", "법인세·연방세, 현지채용인 인건비"),
    ("여비교통비", "현지채용인 식비·일비"),
    ("소모품비(제조용)", ""),
    ("소모품비(Set-up)", "Set up 자재/소모품/안전용품"),
    ("복리후생비", "주재원 상비약, 긴급 의료비"),
    ("수도광열비", ""),
    ("도서인쇄비", ""),
    ("통신비", "USIM 등"),
    ("교육비", "관리자 OSHA 교육 등"),
    ("세금과공과", ""),
    ("보험료", ""),
    ("지급수수료", ""),
    ("지급수수료_보증서", ""),
    ("지급임차료", "숙소/차량/사무실 임대"),
    ("운반비", "현장 지게차 운영"),
    ("현지구매", "현지 구매 품목"),
    ("수출제비용", ""),
    ("잡급", "단기 현지 채용 인건비"),
    ("기타비용", ""),
]


def empty_sheet1():
    """제작비용: 원재료/외주비 라인 (빈 1행씩)"""
    return {
        "material": [_blank_make_row()],   # 원재료
        "outsource": [_blank_make_row()],  # 외주비
    }


def _blank_make_row():
    return {"대분류": "", "중분류": "", "수량": 1, "단가": 0,
            "비고": ""}


def empty_sheet2():
    return [{"구분": n, "금액": 0, "비고": d} for n, d in DIRECT_EXPENSE_ITEMS]


def empty_sheet3():
    return [{"구분": n, "금액": 0, "비고": d} for n, d in LOCAL_OPS_ITEMS]


def _num(v):
    """안전한 float 변환 (NaN/None/빈값 -> 0)"""
    import math
    try:
        f = float(v)
        return 0.0 if math.isnan(f) else f
    except (TypeError, ValueError):
        return 0.0


def sheet1_totals(s1):
    """제작비용 합계: (재료비합계, 외주비합계)"""
    mat = sum(_num(r.get("수량")) * _num(r.get("단가"))
              for r in s1.get("material", []))
    out = sum(_num(r.get("수량")) * _num(r.get("단가"))
              for r in s1.get("outsource", []))
    return mat, out


def sheet_total(rows):
    """직접경비/현지운영비 합계"""
    return sum(_num(r.get("금액")) for r in rows)


# 별첨1 제작비용 컬럼 순서 (대분류>중분류>수량>단가>비고)
MAKE_COLS = ["대분류", "중분류", "수량", "단가", "비고"]
EXPENSE_COLS = ["구분", "금액", "비고"]


def make_df(rows):
    """제작비용 행 리스트를 정해진 컬럼 순서/타입의 DataFrame으로"""
    import pandas as pd
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    df = df.reindex(columns=MAKE_COLS)
    # 타입 명시 (빈 표에서 data_editor 타입 충돌 방지)
    for c in ("대분류", "중분류", "비고"):
        df[c] = df[c].astype("object")
    for c in ("수량", "단가"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).astype(float)
    return df


def expense_df(rows):
    import pandas as pd
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    df = df.reindex(columns=EXPENSE_COLS)
    for c in ("구분", "비고"):
        df[c] = df[c].astype("object")
    df["금액"] = pd.to_numeric(df["금액"], errors="coerce").fillna(0.0).astype(float)
    return df


# 별첨2/3 하단 주석 (예산 수립 시 고려할 세부항목)
EXPENSE_NOTES = [
    "※ 예산 수립시, 고려해야 할 세부항목",
    "여비교통비: 시내교통비, 국내/해외출장비, 차량유류대, 고속도로 통행료, 해외출장시 항공료, 숙박비, 일비 기타여비교통비 등",
    "소모품비: [제조용] 제조용 볼트, 기타 잡자재 등 제조 활동 시 사용되는 소모품",
    "소모품비: [set-up] 현장 Set-up용 자재",
    "복리후생비: 중식대, 외근식대, 주말근무식대, 종합검진비(업무용 검진비, 코로나19 검진비 등) 조직활성화비, 기타복리후생비 등",
    "수도광열비: 도시가스료, 전기료, 수도료, 기타수도광열비",
    "도서인쇄비: 도서구입, 제본비, 기타도서인쇄비 등",
    "통  신  비: 국내/국제/이동전화료, 인터넷사용료, 우편물발송, 그 외 기타 통신비",
    "수  선  비: 기계장치 수리, 기타수선비-PC수리, 기타수선비-기타비품수리 (\"자산\"의 유지관리에 소요되는 경상적인 지출 비용)",
    "세금과공과: 기타 각종세금",
    "보  험  료: 운송/하자이행/계약이행/고용/산재/기타 보험료",
    "지급수수료: 인증(S마크, 기타), 수출중개, 비자발급, 여권발급, 자문료, 그 외 기타지급수수료",
    "지급수수료_보증서: 이행증권 발급(계약이행, 선급금이행, 하자이행 등)",
    "지급임차료: 건물/복사기/팩스/정수기/기타 지급 임차료",
    "운  반  비: 제품/원자재/기타 운반비, 택배/퀵 이용료, 지게차사용료, 도비비, 포장비",
    "수입부대비: 관세, 통관비용, 창고료 등",
    "수출제비용: 통관비용, 창고료 등",
    "잡 급: 국내 일용직 일당(현장청소 용역)",
    "기타비용: 직접경비 중 예비비에 해당하는 항목",
]


def clean_records(records):
    """data_editor 결과의 NaN/numpy 타입을 JSON 직렬화 가능하게 정리"""
    import math
    cleaned = []
    for rec in records:
        new = {}
        for kk, vv in rec.items():
            # numpy 타입 -> python 기본형
            if hasattr(vv, "item"):
                vv = vv.item()
            # NaN -> 적절한 기본값
            if isinstance(vv, float) and math.isnan(vv):
                vv = 0 if kk in ("수량", "단가", "금액") else ""
            if vv is None:
                vv = 0 if kk in ("수량", "단가", "금액") else ""
            new[kk] = vv
        cleaned.append(new)
    return cleaned


def clean_sheet1(s1):
    return {
        "material": clean_records(s1.get("material", [])),
        "outsource": clean_records(s1.get("outsource", [])),
    }
