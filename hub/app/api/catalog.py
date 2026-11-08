"""Spotify-like artist and album pages with library ownership (FR-4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AlbumOut, ArtistPageOut
from app.auth import current_user
from app.catalog.models import ArtistPage, CatalogAlbum, split_ref
from app.db import get_session
from app.library.owned import annotate_albums, annotate_tracks
from app.models import User
from app.services import svc

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _split(ref: str) -> tuple[str, str]:
    try:
        return split_ref(ref)
    except ValueError as e:
        raise HTTPException(400, "Неверная ссылка на каталог") from e


async def get_artist_page(ref: str) -> ArtistPage:
    src, id_ = _split(ref)
    try:
        if src == "dz" and svc.deezer:
            return await svc.deezer.get_artist_page(id_)
        if src == "hm" and svc.hitmoz_catalog:
            return await svc.hitmoz_catalog.get_artist(id_)
    except HTTPException:
        raise
    except Exception as e:
        if type(e).__name__ == "CatalogNotFound":
            raise HTTPException(404, "Исполнитель не найден") from e
        raise HTTPException(502, f"Каталог недоступен: {e}") from e
    raise HTTPException(404, "Исполнитель не найден")


async def get_album(ref: str) -> CatalogAlbum:
    src, id_ = _split(ref)
    try:
        if src == "dz" and svc.deezer:
            return await svc.deezer.get_album(id_)
        if src == "hm" and svc.hitmoz_catalog:
            return await svc.hitmoz_catalog.get_album(id_)
    except Exception as e:
        if type(e).__name__ == "CatalogNotFound":
            raise HTTPException(404, "Альбом не найден") from e
        raise HTTPException(502, f"Каталог недоступен: {e}") from e
    raise HTTPException(404, "Альбом не найден")


@router.get("/artist/{ref}", response_model=ArtistPageOut)
async def artist(ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    page = await get_artist_page(ref)
    albums = await annotate_albums(s, page.albums, user.id)
    albums.sort(key=lambda a: (a.release_date is not None, a.release_date), reverse=True)
    return ArtistPageOut(
        artist=page.artist,
        top_tracks=await annotate_tracks(s, page.top_tracks, user.id),
        albums=[a for a in albums if a.record_type == "album"],
        singles=[a for a in albums if a.record_type in ("single", "ep")],
        compilations=[a for a in albums if a.record_type == "compile"],
    )


@router.get("/album/{ref}", response_model=AlbumOut)
async def album(ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    a = await get_album(ref)
    return (await annotate_albums(s, [a], user.id))[0]
