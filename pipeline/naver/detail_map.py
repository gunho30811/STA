# -*- coding: utf-8 -*-
"""
네이버 상세/단지/학교 API 응답 + 좌표 → naver_listings 한 행으로 매핑 (순수 함수).

DB·네트워크 비의존. crawl_detail.py 가 3개 API를 받아 이 함수로 행을 만든다.
SCHEMA.md 의 naver_listings 컬럼 정의를 그대로 따른다.
"""
import datetime as dt
import json
from subway import nearest_station, stations_within

# 관리비 부과기준 코드 (01/02 정액, 03 확인불가)
_CHARGE = {"01": "정액관리비", "02": "정액관리비", "03": "확인불가"}


def _ymd(s):
    s = str(s or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def map_row(detail, complex_detail, schools, *, region=None):
    """
    detail         : /api/articles/{no} 응답(dict)
    complex_detail : /api/complexes/{c} 의 complexDetail(dict) — 없으면 {}
    schools        : /api/complexes/{c}/schools 의 schools 리스트 — 없으면 []
    region         : {'sido','sigungu','dong','cortarNo'} (listings 에서 가져온 행정구역) — 선택
    """
    AD = detail.get("articleDetail", {})
    ADD = detail.get("articleAddition", {})
    FAC = detail.get("articleFacility", {})
    FL = detail.get("articleFloor", {})
    SP = detail.get("articleSpace", {})
    PR = detail.get("articlePrice", {})
    RT = detail.get("articleRealtor", {})
    TAX = detail.get("articleTax", {})
    ACI = detail.get("administrationCostInfo", {})
    CD = complex_detail or {}
    SCH = (schools or [{}])[0] if schools else {}
    region = region or {}

    no = AD.get("articleNo")
    lat = _float(AD.get("latitude") or CD.get("latitude"))
    lng = _float(AD.get("longitude") or CD.get("longitude"))

    # 층: articleFloor 우선, 없으면 addition 의 floorInfo "현재/총" 파싱
    floor_cur = _int(FL.get("correspondingFloorCount"))
    floor_tot = _int(FL.get("totalFloorCount"))
    fi = str(ADD.get("floorInfo") or "")
    if "/" in fi:
        a, _, b = fi.partition("/")
        floor_cur = floor_cur if floor_cur is not None else _int(a)
        floor_tot = floor_tot if floor_tot is not None else _int(b)

    # 관리비(원) → 만원 (확인 가능할 때만)
    mgmt = None
    if ACI.get("chargeCodeType") in ("01", "02"):
        amt = (ACI.get("etcFeeDetails") or {}).get("etcFeeAmount")
        mgmt = round(amt / 10000) if amt else None

    # 도로명/지번 (단지 API 우선, 없으면 동 수준 노출주소)
    road = " ".join(x for x in [CD.get("roadAddressPrefix"), CD.get("roadAddress")] if x).strip() or None
    jibun = " ".join(x for x in [CD.get("address"), CD.get("detailAddress")] if x).strip() or AD.get("exposureAddress")

    summary = (AD.get("articleFeatureDescription") or ADD.get("articleFeatureDesc") or "").strip()
    facilities = (FAC.get("airconFacilities", []) + FAC.get("lifeFacilities", [])
                  + FAC.get("securityFacilities", []) + FAC.get("etcFacilities", []))
    phones = [p for p in [RT.get("representativeTelNo"), RT.get("cellPhoneNo")] if p]
    approve = _ymd(CD.get("useApproveYmd") or AD.get("aptUseApproveYmd"))

    sub = nearest_station(lat, lng) if (lat and lng) else None

    return {
        "article_no": _int(no),
        "url": f"https://new.land.naver.com/offices?articleNo={no}",
        "building_type_code": AD.get("realestateTypeCode"),
        "building_type": AD.get("realestateTypeName"),
        "confirmed_at": _ymd(AD.get("articleConfirmYMD")),
        "posted_at": _ymd(AD.get("exposeStartYMD")),
        "summary": summary or None,
        "summary_tags": json.dumps([t.strip() for t in summary.split(",") if t.strip()],
                                   ensure_ascii=False) if summary else None,
        "tags": json.dumps(AD.get("tagList"), ensure_ascii=False) if AD.get("tagList") else None,
        # 가격(만원)
        "deposit": _int(PR.get("warrantPrice")),
        "rent_monthly": _int(PR.get("rentPrice")),
        "maintenance_monthly": mgmt,
        "maintenance_type": _CHARGE.get(ACI.get("chargeCodeType")),
        # 면적/구조
        "area_contract_m2": _float(SP.get("supplySpace")),
        "area_exclusive_m2": _float(SP.get("exclusiveSpace")),
        "exclusive_ratio": _int(SP.get("exclusiveRate")),
        "floor_current": floor_cur,
        "floor_total": floor_tot,
        "rooms": _int(AD.get("roomCount")),
        "bathrooms": _int(AD.get("bathroomCount")),
        "direction": (f"{FAC.get('directionTypeName')} ({FAC.get('directionBaseTypeName')})"
                      if FAC.get("directionTypeName") else None),
        "entrance_type": FAC.get("entranceTypeName"),
        "duplex": AD.get("duplexYN") == "Y",
        "move_in": AD.get("moveInTypeName"),
        "facilities": json.dumps(facilities, ensure_ascii=False) if facilities else None,
        # 주소/단지
        "road_address": road,
        "jibun_address": jibun,
        "building_name": CD.get("complexName") or AD.get("aptName"),
        "bldg_dong": AD.get("buildingName") or ADD.get("buildingName"),
        "lat": lat,
        "lng": lng,
        "building_use": AD.get("principalUse"),
        "approval_date": approve,
        "building_age": (dt.datetime.now().year - int(approve[:4])) if approve else None,
        "households": _int(CD.get("totalHouseholdCount") or AD.get("aptHouseholdCount")),
        "households_same_area": _int(AD.get("householdCountByPtp")),
        "heating": (f"{AD.get('aptHeatMethodTypeName')} / {AD.get('aptHeatFuelTypeName')}"
                    if AD.get("aptHeatMethodTypeName") else None),
        "parking_total": _int(CD.get("parkingPossibleCount") or AD.get("aptParkingCount")),
        "parking_per_household": _float(CD.get("parkingCountByHousehold")
                                        or AD.get("aptParkingCountPerHousehold")),
        "floor_area_ratio": _int(CD.get("batlRatio")),
        "building_coverage_ratio": _int(CD.get("btlRatio")),
        "builder": CD.get("constructionCompanyName") or AD.get("aptConstructionCompanyName"),
        "dong_count": _int(CD.get("totalDongCount") or AD.get("totalDongCount")),
        # 중개사
        "agent_office": RT.get("realtorName"),
        "agent_name": RT.get("representativeName"),
        "agent_phone": json.dumps(phones, ensure_ascii=False) if phones else None,
        "agent_address": RT.get("address"),
        "agent_reg_no": RT.get("establishRegistrationNo"),
        "agent_owner_confirmed_3m": _int(RT.get("ownerArticleCount")),
        "broker_fee_max": (round(TAX.get("brokerFee") / 10000, 1) if TAX.get("brokerFee") else None),
        "broker_fee_rate": _float(TAX.get("maxBrokerFee")),
        # 학교
        "school_name": SCH.get("schoolName"),
        "school_type": SCH.get("organizationType"),
        "school_walk_min": _int(SCH.get("walkTime")),
        "school_student_per_teacher": _float(SCH.get("studentCountPerTeacher")),
        # 지하철 (좌표 기반 계산)
        "subway_station": sub["station"] if sub else None,
        "subway_distance_m": sub["distance_m"] if sub else None,
        "subway_500m": json.dumps(stations_within(lat, lng, 500), ensure_ascii=False) if (lat and lng) else None,
        "subway_1km": json.dumps(stations_within(lat, lng, 1000), ensure_ascii=False) if (lat and lng) else None,
        "subway_walk_min": _int(AD.get("walkingTimeToNearSubway")),
        # 같은 건물 같은 면적 매물수 (매물탭 sameAddrCnt)
        "same_building_same_area_count": _int(ADD.get("sameAddrCnt")),
        # 행정구역(필터용)
        "sido": region.get("sido"),
        "sigungu": region.get("sigungu"),
        "dong": region.get("dong"),
        "cortarno": region.get("cortarNo"),
    }


# naver_listings 컬럼 순서 (DDL/INSERT 와 1:1)
COLUMNS = [
    "article_no", "url", "building_type_code", "building_type", "confirmed_at", "posted_at",
    "summary", "summary_tags", "tags", "deposit", "rent_monthly", "maintenance_monthly",
    "maintenance_type", "area_contract_m2", "area_exclusive_m2", "exclusive_ratio",
    "floor_current", "floor_total", "rooms", "bathrooms", "direction", "entrance_type",
    "duplex", "move_in", "facilities", "road_address", "jibun_address", "building_name",
    "bldg_dong", "lat", "lng", "building_use", "approval_date", "building_age", "households",
    "households_same_area", "heating", "parking_total", "parking_per_household",
    "floor_area_ratio", "building_coverage_ratio", "builder", "dong_count",
    "agent_office", "agent_name", "agent_phone", "agent_address", "agent_reg_no",
    "agent_owner_confirmed_3m", "broker_fee_max", "broker_fee_rate", "school_name",
    "school_type", "school_walk_min", "school_student_per_teacher", "subway_station",
    "subway_distance_m", "subway_500m", "subway_1km", "subway_walk_min",
    "same_building_same_area_count", "sido", "sigungu", "dong", "cortarno",
]
