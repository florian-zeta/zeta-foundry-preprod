import httpx
import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter(tags=["campaign"])
logger = logging.getLogger(__name__)


class BuildCampaignRequest(BaseModel):
    site_id: str = Field(..., example="foundry")
    api_key: str = Field(..., example="your-api-key-here")
    campaign_name: str = Field(..., example="True Religion — Abandoned Cart — 1746321234")
    subject_line: str = Field(..., example="You left something behind")
    preheader_text: Optional[str] = Field(None)
    from_email: str = Field("foundry@zetademos.com")
    from_name: str = Field(..., example="True Religion")
    segment_name: Optional[str] = Field(None, description="Segment name for audience include")
    snippet_names: list[str] = Field(..., description="Ordered snippet names for message body")


class BuildCampaignResponse(BaseModel):
    site_id: str
    campaign_name: str
    campaign_id: Optional[str]
    status: str
    error: Optional[str]


def _assemble_message_html(snippet_names: list[str]) -> str:
    lines = ['<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="margin:0;padding:0;">']
    for name in snippet_names:
        lines.append(f"{{% snippet name: '{name}' %}}")
    lines.append('</body></html>')
    return '\n'.join(lines)


@router.post(
    "/build-campaign",
    response_model=BuildCampaignResponse,
    summary="Create a ZMP broadcast campaign and set HTML content",
)
async def build_campaign(req: BuildCampaignRequest):
    auth = ("api", req.api_key)
    base_url = f"https://api.zetaglobal.net/ver2/{req.site_id}"
    message_html = _assemble_message_html(req.snippet_names)

    logger.info(f"build_campaign: site={req.site_id} name={req.campaign_name}")

    # Step 1 — Create campaign shell
    payload = {
        "campaign": {
            "status": "draft",
            "transactional_flag": False,
            "campaign_name": req.campaign_name,
            "versions": [
                {
                    "channel": "email",
                    "version_name": "1",
                    **({"audience": {"includes": [req.segment_name]}} if req.segment_name else {}),
                    "variates": [
                        {
                            "variate_name": "a",
                            "test_distribution": 100,
                            "from_name": req.from_name,
                            "from_address": req.from_email,
                            "reply_to_name": req.from_name,
                            "reply_to_address": req.from_email,
                            "subject": req.subject_line,
                        }
                    ]
                }
            ]
        }
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{base_url}/broadcasts/",
            json=payload,
            auth=auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"}
        )

    logger.info(f"build_campaign create: {response.status_code} {response.text[:300]}")

    if response.status_code not in (200, 201):
        return BuildCampaignResponse(
            site_id=req.site_id,
            campaign_name=req.campaign_name,
            campaign_id=None,
            status="failed",
            error=f"HTTP {response.status_code} — {response.text[:300]}"
        )

    data = response.json()
    campaign_id = str(
        data.get("id") or
        data.get("campaign_id") or
        data.get("campaign", {}).get("id") or ""
    )

    logger.info(f"build_campaign created id={campaign_id}")

    # Step 2 — Update campaign content with snippet HTML
    if campaign_id:
        content_payload = {
            "versions": [
                {
                    "label": "1",
                    "variates": [
                        {
                            "label": "A",
                            "html": message_html
                        }
                    ]
                }
            ]
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            content_response = await client.patch(
                f"{base_url}/campaigns/{campaign_id}/content",
                json=content_payload,
                auth=auth,
                headers={"Accept": "application/json", "Content-Type": "application/json"}
            )
        logger.info(f"build_campaign content update: {content_response.status_code} {content_response.text[:200]}")

    return BuildCampaignResponse(
        site_id=req.site_id,
        campaign_name=req.campaign_name,
        campaign_id=campaign_id,
        status="created",
        error=None
    )