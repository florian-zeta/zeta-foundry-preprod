import httpx
import asyncio
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter(tags=["snippets"])
logger = logging.getLogger(__name__)


def _sanitize_name(name: str) -> str:
    """Sanitize snippet name — replace spaces and dashes with underscores, lowercase."""
    return name.strip().lower().replace(" ", "_").replace("-", "_").replace("—", "_")


class SnippetItem(BaseModel):
    name: str = Field(..., description="Snippet name — use underscores, no spaces or dashes")
    html: str = Field(..., description="Full HTML content for the snippet")


class BuildSnippetsRequest(BaseModel):
    site_id: str = Field(..., example="foundry")
    api_key: str = Field(..., example="your-api-key-here")
    snippets: list[SnippetItem] = Field(
        ...,
        description="Ordered list of snippets to create or update"
    )


class SnippetResult(BaseModel):
    name: str
    status: str  # created | updated | failed
    snippet_id: Optional[str] = None
    error: Optional[str] = None


class BuildSnippetsResponse(BaseModel):
    site_id: str
    total: int
    succeeded: int
    failed: int
    results: list[SnippetResult]
    snippet_names: list[str]


async def _get_snippet_by_name(
    client: httpx.AsyncClient,
    base_url: str,
    auth: tuple,
    name: str
) -> Optional[dict]:
    try:
        response = await client.get(
            f"{base_url}/snippets",
            auth=auth,
            params={"name": name},
            timeout=10.0,
            headers={"Accept": "application/json"}
        )
        if response.status_code == 200:
            data = response.json()
            snippets = data.get("snippets") or data.get("data") or []
            for s in snippets:
                if s.get("name") == name:
                    return s
    except Exception:
        pass
    return None


async def _create_snippet(
    client: httpx.AsyncClient,
    base_url: str,
    auth: tuple,
    name: str,
    html: str
) -> SnippetResult:
    try:
        response = await client.post(
            f"{base_url}/snippets",
            json={"name": name, "html": html},
            auth=auth,
            timeout=15.0,
            headers={"Accept": "application/json", "Content-Type": "application/json"}
        )
        if response.status_code in (200, 201):
            data = response.json()
            snippet_id = str(data.get("id") or data.get("snippet_id") or "")
            return SnippetResult(name=name, status="created", snippet_id=snippet_id)
        else:
            return SnippetResult(name=name, status="failed",
                                 error=f"HTTP {response.status_code} — {response.text[:200]}")
    except Exception as e:
        return SnippetResult(name=name, status="failed", error=str(e)[:200])


async def _update_snippet(
    client: httpx.AsyncClient,
    base_url: str,
    auth: tuple,
    snippet_id: str,
    name: str,
    html: str
) -> SnippetResult:
    try:
        response = await client.patch(
            f"{base_url}/snippets/{snippet_id}",
            json={"name": name, "html": html},
            auth=auth,
            timeout=15.0,
            headers={"Accept": "application/json", "Content-Type": "application/json"}
        )
        if response.status_code in (200, 201):
            return SnippetResult(name=name, status="updated", snippet_id=snippet_id)
        else:
            return SnippetResult(name=name, status="failed",
                                 error=f"HTTP {response.status_code} — {response.text[:200]}")
    except Exception as e:
        return SnippetResult(name=name, status="failed", error=str(e)[:200])


@router.post(
    "/build-snippets",
    response_model=BuildSnippetsResponse,
    summary="Create or update ZMP snippets via REST API",
    description=(
        "Creates or updates ZMP email snippets directly via the ZMP REST API. "
        "Checks if snippet exists by name first — updates if found, creates if not. "
        "Returns snippet names for use in /build-template."
    )
)
async def build_snippets(req: BuildSnippetsRequest):
    if not req.snippets:
        raise HTTPException(status_code=400, detail="No snippets provided")

    base_url = f"https://api.zetaglobal.net/ver2/{req.site_id}"
    auth = ("api", req.api_key)

    logger.info(f"build_snippets: site={req.site_id} count={len(req.snippets)}")

    results = []
    async with httpx.AsyncClient() as client:
        for snippet in req.snippets:
            # Sanitize name — no spaces or dashes allowed
            snippet.name = _sanitize_name(snippet.name)
            # Check if exists
            existing = await _get_snippet_by_name(client, base_url, auth, snippet.name)
            if existing:
                snippet_id = str(existing.get("id") or existing.get("snippet_id") or "")
                result = await _update_snippet(client, base_url, auth, snippet_id, snippet.name, snippet.html)
            else:
                result = await _create_snippet(client, base_url, auth, snippet.name, snippet.html)
            results.append(result)
            logger.info(f"snippet '{snippet.name}': {result.status}")

    succeeded = sum(1 for r in results if r.status in ("created", "updated"))
    failed = sum(1 for r in results if r.status == "failed")
    snippet_names = [r.name for r in results if r.status in ("created", "updated")]

    return BuildSnippetsResponse(
        site_id=req.site_id,
        total=len(req.snippets),
        succeeded=succeeded,
        failed=failed,
        results=results,
        snippet_names=snippet_names
    )