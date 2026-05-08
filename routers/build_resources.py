import httpx
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter(tags=["resources"])


class BuildResourcesRequest(BaseModel):
    site_id: str = Field(..., example="buffalo-wild-wings-crm-demo-2025")
    api_key: str = Field(..., example="your-api-key-here")
    brand_name: str = Field(..., example="Buffalo Wild Wings")
    vertical: str = Field(..., example="retail")
    resource_types: list[str] = Field(
        ...,
        description="Resource types to generate e.g. ['product', 'location']",
        example=["product", "location"]
    )
    items_per_type: int = Field(
        9,
        ge=1,
        le=20,
        description="Items per type — 9 minimum for recommendations engine"
    )
    product_names: Optional[list[str]] = Field(
        None,
        description="Product/service names from brand research"
    )
    location_names: Optional[list[str]] = Field(
        None,
        description="Location city names from brand research e.g. ['Nashville TN', 'Austin TX']"
    )


class BuildResourcesResponse(BaseModel):
    site_id: str
    total: int
    succeeded: int
    failed: int
    resource_counts: dict
    errors: list[str]


def _default_product_names(vertical: str) -> list[str]:
    defaults = {
        "retail": [
            "Classic Wings", "Boneless Wings", "Loaded Nachos",
            "Buffalo Wrap", "Chicken Tenders", "Cheese Curds",
            "Soft Pretzel", "Street Tacos", "Mozzarella Sticks"
        ],
        "financial_services": [
            "Checking Account", "Savings Account", "Credit Card",
            "Home Mortgage", "Auto Loan", "Personal Loan",
            "Investment Account", "Business Checking", "CD Account"
        ],
        "healthcare": [
            "Annual Wellness Visit", "Telehealth Consultation", "Physical Therapy",
            "Mental Health Services", "Urgent Care Visit", "Preventive Screening",
            "Chronic Care Management", "Specialist Referral", "Lab Services"
        ],
        "hr_software": [
            "Core HR Module", "Payroll Processing", "Benefits Administration",
            "Performance Management", "Onboarding Suite", "Learning Management",
            "Analytics Dashboard", "Compliance Tools", "Mobile App Access"
        ],
        "b2b": [
            "Core Platform", "Analytics Dashboard", "Onboarding Suite",
            "Compliance Tools", "Integration Layer", "Reporting Module",
            "Admin Console", "Mobile App", "API Access"
        ],
    }
    return defaults.get(vertical, defaults["retail"])


def _default_location_names() -> list[str]:
    return [
        "Downtown", "Westside", "Northgate", "South Plaza",
        "East Village", "Midtown", "Airport", "University District", "Lakeside"
    ]


def _generate_items(
    brand_name: str,
    vertical: str,
    resource_type: str,
    count: int,
    product_names: Optional[list[str]],
    location_names: Optional[list[str]],
    run_ts: str
) -> list[dict]:
    if resource_type == "location":
        names = (location_names or _default_location_names())[:count]
    else:
        names = (product_names or _default_product_names(vertical))[:count]

    items = []
    for i, name in enumerate(names):
        idx = str(i + 1).zfill(3)
        encoded = name.replace(" ", "+")
        resource_id = f"foundry_{resource_type}_{idx}_{run_ts}"
        items.append({
            "resource-id": resource_id,
            "resource-type": resource_type,
            "title": f"{brand_name} — {name}" if resource_type == "location" else name,
            "description": f"{brand_name} location in {name}" if resource_type == "location" else f"{name} — {brand_name}",
            "body": f"Visit {brand_name} in {name}." if resource_type == "location" else f"{name} from {brand_name}.",
            "url": f"https://www.{brand_name.lower().replace(' ', '')}.com/{'locations' if resource_type == 'location' else 'menu'}",
            "thumbnail": f"https://placehold.co/400x300/333333/ffffff?text={encoded}",
            "brand": brand_name,
        })
    return items


async def _put_resource(
    client: httpx.AsyncClient,
    base_url: str,
    auth: tuple,
    item: dict
) -> tuple[bool, Optional[str]]:
    url = f"{base_url}/{item['resource-id']}"
    try:
        response = await client.put(
            url,
            json=item,
            auth=auth,
            timeout=15.0,
            headers={"Accept": "application/json"}
        )
        if response.status_code in (200, 201):
            return True, None
        else:
            return False, f"{item['resource-id']}: HTTP {response.status_code} — {response.text[:100]}"
    except Exception as e:
        return False, f"{item['resource-id']}: {str(e)[:100]}"


@router.post(
    "/build-resources",
    response_model=BuildResourcesResponse,
    summary="Generate and load resources into ZMP",
    description=(
        "Generates product and/or location resources with basic fields "
        "and PUTs them directly to ZMP. Pass product_names and location_names "
        "from agent brand research for accurate results. "
        "Minimum 9 items per type for recommendations engine."
    )
)
async def build_resources(req: BuildResourcesRequest):
    run_ts = str(int(datetime.now(timezone.utc).timestamp()))
    auth = ("api", req.api_key)
    base_url = f"https://api.zetaglobal.net/ver2/{req.site_id}/resources"

    all_items = []
    for rtype in req.resource_types:
        all_items.extend(_generate_items(
            req.brand_name, req.vertical, rtype,
            req.items_per_type, req.product_names,
            req.location_names, run_ts
        ))

    if not all_items:
        raise HTTPException(status_code=400, detail="No resource types recognized")

    succeeded = 0
    failed = 0
    errors = []
    resource_counts: dict = {}

    async with httpx.AsyncClient() as client:
        tasks = [_put_resource(client, base_url, auth, item) for item in all_items]
        results = await asyncio.gather(*tasks)
        for item, (success, error) in zip(all_items, results):
            if success:
                succeeded += 1
                resource_counts[item["resource-type"]] = resource_counts.get(item["resource-type"], 0) + 1
            else:
                failed += 1
                if error:
                    errors.append(error)

    return BuildResourcesResponse(
        site_id=req.site_id,
        total=len(all_items),
        succeeded=succeeded,
        failed=failed,
        resource_counts=resource_counts,
        errors=errors[:10]
    )