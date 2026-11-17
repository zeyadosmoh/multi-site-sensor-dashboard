"""The 13 fictional sites (SYNTHETIC). Names are invented; coordinates sit in real industrial/commercial areas
of each city so the map looks right. Climate numbers are rough monthly outdoor means (May..Oct) and diurnal
half-amplitudes, used only to shape synthetic indoor readings."""

from dataclasses import dataclass

MONTHS = ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10")


@dataclass(frozen=True)
class Site:
    code: str
    name: str
    city: str
    kind: str  # warehouse | branch
    lat: float
    lon: float
    # outdoor climate: monthly mean temperature May..Oct, diurnal half-amplitude, mean outdoor RH
    t_out: tuple[float, ...]
    t_amp: float
    rh_out: float

    @property
    def thresholds(self) -> dict:
        if self.kind == "warehouse":
            # Red Sea coast: management accepted a higher humidity ceiling for the Jeddah warehouse
            return {"temp_min": 15.0, "temp_max": 30.0, "hum_min": 20.0,
                    "hum_max": 70.0 if self.city == "Jeddah" else 65.0}
        return {"temp_min": 18.0, "temp_max": 27.0, "hum_min": 25.0, "hum_max": 60.0}


SITES: tuple[Site, ...] = (
    Site("RUH-01", "Riyadh Central Warehouse", "Riyadh", "warehouse", 24.59270, 46.82670,
         (33.0, 35.5, 37.0, 36.5, 33.0, 27.5), 7.0, 15),
    Site("RUH-02", "Riyadh Sulay Warehouse", "Riyadh", "warehouse", 24.56820, 46.85410,
         (33.0, 35.5, 37.0, 36.5, 33.0, 27.5), 7.0, 15),
    Site("RUH-03", "Riyadh Olaya Branch", "Riyadh", "branch", 24.69430, 46.68530,
         (33.0, 35.5, 37.0, 36.5, 33.0, 27.5), 7.0, 15),
    Site("JED-01", "Jeddah Industrial Warehouse", "Jeddah", "warehouse", 21.40580, 39.24120,
         (30.0, 31.5, 32.5, 33.0, 32.0, 30.0), 4.0, 62),
    Site("JED-02", "Jeddah Al Rawdah Branch", "Jeddah", "branch", 21.56220, 39.15310,
         (30.0, 31.5, 32.5, 33.0, 32.0, 30.0), 4.0, 62),
    Site("DMM-01", "Dammam Second Industrial Warehouse", "Dammam", "warehouse", 26.31700, 50.07100,
         (32.0, 35.0, 36.5, 36.5, 33.5, 29.5), 6.0, 50),
    Site("KHB-01", "Al Khobar Corniche Branch", "Al Khobar", "branch", 26.27940, 50.20830,
         (32.0, 35.0, 36.5, 36.5, 33.5, 29.5), 5.5, 55),
    Site("MAK-01", "Makkah Logistics Warehouse", "Makkah", "warehouse", 21.36020, 39.78630,
         (34.0, 35.5, 36.0, 35.5, 35.0, 32.0), 6.0, 30),
    Site("MED-01", "Madinah Industrial Warehouse", "Madinah", "warehouse", 24.43860, 39.67500,
         (32.0, 35.0, 36.0, 36.0, 34.0, 29.0), 7.0, 15),
    Site("HOF-01", "Al Hofuf Distribution Warehouse", "Al Hofuf", "warehouse", 25.36500, 49.58700,
         (34.5, 37.5, 39.0, 38.5, 35.5, 30.0), 8.0, 22),
    Site("BUR-01", "Buraidah Qassim Warehouse", "Buraidah", "warehouse", 26.35920, 43.98180,
         (31.5, 35.0, 36.5, 36.0, 32.5, 26.5), 8.0, 15),
    Site("TAB-01", "Tabuk City Branch", "Tabuk", "branch", 28.38380, 36.55500,
         (27.0, 30.0, 32.0, 32.0, 29.0, 24.0), 8.0, 20),
    Site("ABH-01", "Abha Highlands Branch", "Abha", "branch", 18.21640, 42.50530,
         (22.0, 24.0, 24.0, 24.0, 21.5, 18.0), 5.0, 40),
)

BY_CODE = {s.code: s for s in SITES}

# Arabic display names (SYNTHETIC site names; real city names)
NAMES_AR = {
    "RUH-01": "مستودع الرياض المركزي",
    "RUH-02": "مستودع السلي بالرياض",
    "RUH-03": "فرع العليا بالرياض",
    "JED-01": "مستودع جدة الصناعي",
    "JED-02": "فرع الروضة بجدة",
    "DMM-01": "مستودع المدينة الصناعية الثانية بالدمام",
    "KHB-01": "فرع كورنيش الخبر",
    "MAK-01": "مستودع مكة المكرمة اللوجستي",
    "MED-01": "مستودع المدينة المنورة الصناعي",
    "HOF-01": "مستودع التوزيع بالهفوف",
    "BUR-01": "مستودع بريدة بالقصيم",
    "TAB-01": "فرع مدينة تبوك",
    "ABH-01": "فرع مرتفعات أبها",
}
CITIES_AR = {
    "Riyadh": "الرياض", "Jeddah": "جدة", "Dammam": "الدمام", "Al Khobar": "الخبر", "Makkah": "مكة المكرمة",
    "Madinah": "المدينة المنورة", "Al Hofuf": "الهفوف", "Buraidah": "بريدة", "Tabuk": "تبوك", "Abha": "أبها",
}
