"""Mock Cornell `/api/entries` server for local development.

Run alongside the reporter when the real Cornell instance isn't available:

    python scripts/seed_mock_data.py            # serves on :8001

The fixture covers the full categorization matrix (todos / concerns /
successes / general) so downstream modules have realistic data to chew on.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Header, HTTPException, Query  # noqa: E402

MOCK_ENTRIES_RAW: list[dict] = [
    {
        "offset_days": 0,
        "cue_column": "Sabah planı",
        "notes_column": "Bugün backend için endpoint yazacağım. Ekibe sunum yapmalıyım.",
        "summary": "Üretken bir gün başladı.",
        "planlar": "[ ] Endpoint yaz\n[ ] Sunumu hazırla",
    },
    {
        "offset_days": 1,
        "cue_column": "Endişeler",
        "notes_column": "Sunum stresi yüzünden uyuyamadım. Performans değerlendirmesinden korkuyorum.",
        "summary": "Kaygı yoğun.",
        "planlar": "[x] Sunum dosyasını gözden geçir\n[ ] Yarına ertelendi: prova",
    },
    {
        "offset_days": 2,
        "cue_column": "Başarılar",
        "notes_column": "Sunumu başardım, sorulara güzel cevap verdim. İlk kez büyük bir grup önünde rahattım.",
        "summary": "Mutluyum, harikaydı.",
        "planlar": "[x] Sunum",
    },
    {
        "offset_days": 3,
        "cue_column": "Genel düşünceler",
        "notes_column": (
            "Bugün üzerine çok düşündüğüm şey, projelerin gerçekten bitmesinin sadece "
            "başlangıçtan değil, küçük tamamlama anlarından beslendiğiydi. Bu farkındalık "
            "önümüzdeki haftaya bakışımı değiştiriyor."
        ),
        "summary": "Yansıma günü.",
        "planlar": "",
    },
    {
        "offset_days": 4,
        "cue_column": "Hatalar",
        "notes_column": "Deploy sırasında env dosyasını unuttum. Yapamadım, hata yaptım.",
        "summary": "Ders aldım.",
        "planlar": "[ ] Pre-deploy checklist yaz",
    },
]


def _materialize(reference_date: date) -> list[dict]:
    out = []
    for idx, raw in enumerate(MOCK_ENTRIES_RAW, start=1):
        d = reference_date - timedelta(days=raw["offset_days"])
        out.append(
            {
                "id": idx,
                "date": d.isoformat(),
                "cue_column": raw["cue_column"],
                "notes_column": raw["notes_column"],
                "summary": raw["summary"],
                "planlar": raw["planlar"],
                "created_at": datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
                "updated_at": datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
            }
        )
    return out


def create_mock_app(api_key: str = "test-cornell-key") -> FastAPI:
    app = FastAPI(title="Cornell Journal Mock", version="0.1.0")

    @app.get("/api/entries")
    async def get_entries(
        start: date | None = Query(None),
        end: date | None = Query(None),
        fetch_all: bool = Query(False),
        x_api_key: str | None = Header(None, alias="X-API-Key"),
    ):
        if x_api_key != api_key:
            raise HTTPException(status_code=401, detail="invalid api key")

        today = date.today()
        all_entries = _materialize(today)

        if fetch_all:
            selected = all_entries
            range_start = min(date.fromisoformat(e["date"]) for e in selected) if selected else today
            range_end = max(date.fromisoformat(e["date"]) for e in selected) if selected else today
        else:
            if start is None or end is None:
                end = today
                start = today - timedelta(days=30)
            selected = [
                e for e in all_entries if start <= date.fromisoformat(e["date"]) <= end
            ]
            range_start, range_end = start, end

        return {
            "entries": selected,
            "count": len(selected),
            "range": {"start": range_start.isoformat(), "end": range_end.isoformat()},
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run mock Cornell endpoint")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(create_mock_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
