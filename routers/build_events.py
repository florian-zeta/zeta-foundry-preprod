import httpx
import asyncio
import random
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter(tags=["events"])
logger = logging.getLogger(__name__)

# Defaults used when agent doesn't pass explicit values
DEFAULT_LIGHT_EVENTS = {
    "retail": ["page_view", "product_view", "add_to_cart"],
    "financial_services": ["page_view", "product_view", "login"],
    "healthcare": ["page_view", "appointment_viewed", "portal_login"],
    "b2b": ["page_view", "pricing_viewed", "whitepaper_downloaded"],
    "hr_software": ["page_view", "pricing_viewed", "whitepaper_downloaded"],
}

DEFAULT_RICH_EVENT = {
    "retail": ("updated_cart", "items"),
    "financial_services": ("product_inquiry", "products"),
    "healthcare": ("appointment_booked", "services"),
    "b2b": ("demo_requested", "products"),
    "hr_software": ("demo_requested", "products"),
}

DEFAULT_CATALOG = {
    "retail": [
        {"name": "Classic Wings", "price": 14.99},
        {"name": "Boneless Wings", "price": 13.99},
        {"name": "Loaded Nachos", "price": 12.99},
        {"name": "Buffalo Wrap", "price": 11.99},
        {"name": "Chicken Tenders", "price": 12.99},
    ],
    "financial_services": [
        {"name": "Checking Account", "price": 0},
        {"name": "Savings Account", "price": 0},
        {"name": "Credit Card", "price": 0},
    ],
    "healthcare": [
        {"name": "Annual Wellness Visit", "price": 0},
        {"name": "Telehealth Consultation", "price": 75},
        {"name": "Physical Therapy", "price": 120},
    ],
    "b2b": [
        {"name": "Core Module", "price": 299},
        {"name": "Analytics Dashboard", "price": 199},
        {"name": "Onboarding Suite", "price": 149},
    ],
}


class CatalogItem(BaseModel):
    name: str
    image: Optional[str] = None
    url: Optional[str] = None
    price: Optional[float] = None


class BuildEventsRequest(BaseModel):
    site_id: str = Field(..., example="buffalo-wild-wings-crm-demo-2025")
    api_key: str = Field(..., example="your-api-key-here")
    uids: list[str] = Field(..., description="Subscriber UIDs from /load-audience")
    brand_name: str = Field(..., example="Buffalo Wild Wings")
    brand_url: Optional[str] = Field(None, example="https://www.buffalowildwings.com")
    vertical: Optional[str] = Field(None, example="retail")
    # All fields below are optional — foundry uses smart defaults if not provided
    light_event_names: Optional[list[str]] = Field(None)
    rich_event_name: Optional[str] = Field(None)
    rich_items_key: Optional[str] = Field(None)
    catalog: Optional[list[CatalogItem]] = Field(None)
    product_names: Optional[list[str]] = Field(
        None,
        description="Brand product names to use in event catalog"
    )
    events_per_user: int = Field(2, ge=1, le=5)


class BuildEventsResponse(BaseModel):
    site_id: str
    total: int
    succeeded: int
    failed: int
    event_counts: dict
    rich_event_name: str
    rich_items_key: str
    errors: list[str]


def _random_past_date(rng: random.Random, days_ago_min: int, days_ago_max: int) -> str:
    days = rng.randint(days_ago_min, days_ago_max)
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_vertical(vertical: Optional[str]) -> str:
    if not vertical:
        return "retail"
    v = vertical.lower()
    if v in DEFAULT_LIGHT_EVENTS:
        return v
    # Map common aliases
    mapping = {
        "hr_software": "b2b",
        "saas": "b2b",
        "enterprise": "b2b",
        "casual_dining": "retail",
        "restaurant": "retail",
        "ecommerce": "retail",
        "fintech": "financial_services",
        "banking": "financial_services",
        "wellness": "healthcare",
        "clinic": "healthcare",
    }
    return mapping.get(v, "retail")


def _build_catalog(
    vertical: str,
    brand_name: str,
    brand_url: str,
    catalog: Optional[list[CatalogItem]]
) -> list[dict]:
    if catalog:
        return [
            {
                "name": item.name,
                "price": item.price or 0,
                "image": item.image or f"https://placehold.co/400x300/333333/ffffff?text={item.name.replace(' ', '+')}",
                "url": item.url or brand_url,
            }
            for item in catalog
        ]
    # Fall back to defaults
    defaults = DEFAULT_CATALOG.get(vertical, DEFAULT_CATALOG["retail"])
    return [
        {
            "name": item["name"],
            "price": item["price"],
            "image": f"https://placehold.co/400x300/333333/ffffff?text={item['name'].replace(' ', '+')}",
            "url": brand_url,
        }
        for item in defaults
    ]


def _build_light_activity(uid, event, brand_name, brand_url, rng, timestamp):
    return {
        "activity": {
            "subscriber": {"uid": uid},
            "event": event,
            "timestamp": timestamp,
            "properties": {
                "source": "zeta-sandbox-foundry",
                "brand": brand_name,
                "url": brand_url,
            }
        }
    }


def _build_rich_activity(uid, rich_event_name, rich_items_key, brand_name, brand_url, catalog, rng, timestamp):
    n_items = rng.randint(1, min(3, len(catalog)))
    selected = rng.sample(catalog, n_items)
    items = [
        {
            "name": item["name"],
            "quantity": rng.randint(1, 2),
            "price": item.get("price") or 0,
            "image": item["image"],
            "url": item["url"],
        }
        for item in selected
    ]
    cart_value = round(sum(i["price"] * i["quantity"] for i in items), 2)
    return {
        "activity": {
            "subscriber": {"uid": uid},
            "event": rich_event_name,
            "timestamp": timestamp,
            "properties": {
                "source": "zeta-sandbox-foundry",
                "brand": brand_name,
                "brand_url": brand_url,
                "cart_id": f"CART-{uid[-8:]}-{rng.randint(1000, 9999)}",
                "cart_value": cart_value,
                "currency": "USD",
                rich_items_key: items,
            }
        }
    }


async def _post_event(client, url, auth, payload):
    event_name = payload["activity"]["event"]
    try:
        response = await client.post(
            url, json=payload, auth=auth, timeout=15.0,
            headers={"Accept": "application/json"}
        )
        if response.status_code in (200, 201, 202):
            return True, event_name, None
        else:
            return False, event_name, f"HTTP {response.status_code} — {response.text[:100]}"
    except Exception as e:
        return False, event_name, str(e)[:100]


@router.post(
    "/build-events",
    response_model=BuildEventsResponse,
    summary="Generate behavioral events including one rich loopable event per subscriber",
    description=(
        "Creates light behavioral events plus one rich self-contained event per subscriber. "
        "All fields except site_id, api_key, uids, and brand_name are optional — "
        "foundry uses smart vertical-based defaults if not provided."
    )
)
async def build_events(req: BuildEventsRequest):
    if not req.uids:
        raise HTTPException(status_code=400, detail="No UIDs provided")

    v = _resolve_vertical(req.vertical)
    brand_url = req.brand_url or f"https://www.{req.brand_name.lower().replace(' ', '')}.com"

    light_events = req.light_event_names or DEFAULT_LIGHT_EVENTS.get(v, ["page_view", "view_menu"])
    default_rich, default_key = DEFAULT_RICH_EVENT.get(v, ("updated_cart", "items"))
    rich_event_name = req.rich_event_name or default_rich
    rich_items_key = req.rich_items_key or default_key

    # Build catalog from product_names if provided — do this BEFORE _build_catalog
    if req.product_names and not req.catalog:
        req.catalog = [
            CatalogItem(
                name=name,
                price=None,
                image=f"https://placehold.co/400x300/333333/ffffff?text={name.replace(' ', '+')}",
                url=req.brand_url or f"https://www.{req.brand_name.lower().replace(' ', '')}.com"
            )
            for name in req.product_names
        ]

    catalog = _build_catalog(v, req.brand_name, brand_url, req.catalog)

    logger.info(
        f"build_events: site={req.site_id} uids={len(req.uids)} "
        f"catalog={len(catalog)} rich_event={rich_event_name} vertical={v}"
    )

    url = f"https://api.zetaglobal.net/ver2/{req.site_id}/activities"
    auth = ("api", req.api_key)

    payloads = []
    for uid in req.uids:
        rng = random.Random(uid)
        for i in range(req.events_per_user):
            event = rng.choice(light_events)
            ts = _random_past_date(rng, i * 7 + 14, i * 7 + 30)
            payloads.append(_build_light_activity(uid, event, req.brand_name, brand_url, rng, ts))
        rich_ts = _random_past_date(rng, 1, 7)
        payloads.append(_build_rich_activity(
            uid, rich_event_name, rich_items_key,
            req.brand_name, brand_url, catalog, rng, rich_ts
        ))

    logger.info(f"build_events: {len(payloads)} payloads to send")

    succeeded = 0
    failed = 0
    errors = []
    event_counts: dict = {}

    async with httpx.AsyncClient() as client:
        tasks = [_post_event(client, url, auth, p) for p in payloads]
        results = await asyncio.gather(*tasks)
        for success, event_name, error in results:
            if success:
                succeeded += 1
                event_counts[event_name] = event_counts.get(event_name, 0) + 1
            else:
                failed += 1
                if error:
                    errors.append(error)

    logger.info(f"build_events: succeeded={succeeded} failed={failed}")

    return BuildEventsResponse(
        site_id=req.site_id,
        total=len(payloads),
        succeeded=succeeded,
        failed=failed,
        event_counts=event_counts,
        rich_event_name=rich_event_name,
        rich_items_key=rich_items_key,
        errors=errors[:10]
    )