import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter(tags=["template"])


class BuildTemplateRequest(BaseModel):
    site_id: str = Field(..., example="buffalo-wild-wings-crm-demo-2025")
    api_key: str = Field(..., example="your-api-key-here")
    name: str = Field(..., example="True Religion — Abandoned Cart")
    snippet_names: list[str] = Field(
        ...,
        description="Ordered list of snippet names to include in template",
        example=["header", "hero — abandoned cart", "cart — True Religion", "footer"]
    )
    subject_line: Optional[str] = Field(None, example="You left something behind")


class BuildTemplateResponse(BaseModel):
    site_id: str
    name: str
    template_id: Optional[str]
    status: str
    error: Optional[str]


def _build_template_html(snippet_names: list[str]) -> str:
    """
    Build a ZMP template that references snippets via {% snippet %} tags.
    Each snippet is dropped in order — no wrapper needed.
    """
    lines = ['<!DOCTYPE html>', '<html>', '<head>',
             '<meta charset="UTF-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
             '</head>', '<body style="margin:0;padding:0;">']

    for name in snippet_names:
        lines.append(f'{{% snippet "{name}" %}}')

    lines.extend(['</body>', '</html>'])
    return '\n'.join(lines)


@router.post(
    "/build-template",
    response_model=BuildTemplateResponse,
    summary="Create a ZMP email template from ordered snippets",
    description=(
        "Creates a ZMP template that references snippets in order via ZML snippet tags. "
        "Snippets must already exist in ZMP before calling this endpoint."
    )
)
async def build_template(req: BuildTemplateRequest):
    html = _build_template_html(req.snippet_names)

    url = f"https://api.zetaglobal.net/ver2/{req.site_id}/templates"
    auth = ("api", req.api_key)

    payload = {"name": req.name, "html": html}

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            url, json=payload, auth=auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"}
        )

    if response.status_code in (200, 201):
        data = response.json()
        template_id = str(data.get("id") or data.get("template_id") or "")
        return BuildTemplateResponse(
            site_id=req.site_id,
            name=req.name,
            template_id=template_id,
            status="created",
            error=None
        )
    else:
        return BuildTemplateResponse(
            site_id=req.site_id,
            name=req.name,
            template_id=None,
            status="failed",
            error=f"HTTP {response.status_code} — {response.text[:200]}"
        )
