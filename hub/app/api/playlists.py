from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.medialib import nd_call, songs_out
from app.api.schemas import PlaylistAddIn, PlaylistCreateIn, PlaylistOut, PlaylistPageOut, PlaylistRemoveIn
from app.auth import current_user, user_navidrome
from app.db import get_session
from app.models import Track, User

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def pl_out(p: dict) -> PlaylistOut:
    return PlaylistOut(id=p["id"], name=p.get("name", ""), song_count=p.get("songCount", 0),
                       duration_s=p.get("duration", 0), cover_art=p.get("coverArt"))


@router.get("", response_model=list[PlaylistOut])
async def list_playlists(user: User = Depends(current_user)):
    nd = user_navidrome(user)
    try:
        pls = await nd.get_playlists()
    finally:
        await nd.aclose()
    # getPlaylists also returns other users' public playlists: show only own ones
    return [pl_out(p) for p in pls if p.get("owner") in (None, user.nd_username)]


@router.post("", response_model=PlaylistOut)
async def create_playlist(body: PlaylistCreateIn, user: User = Depends(current_user)):
    if not body.name.strip():
        raise HTTPException(400, "Введите название")
    nd = user_navidrome(user)
    try:
        p = await nd.create_playlist(body.name.strip())
    finally:
        await nd.aclose()
    return pl_out(p)


@router.post("/{playlist_id}/add")
async def add(playlist_id: str, body: PlaylistAddIn, user: User = Depends(current_user),
              s: AsyncSession = Depends(get_session, scope="function")):
    ids = list(body.song_ids)
    if body.library_track_ids:
        rows = (await s.execute(select(Track.id, Track.nd_song_id).where(Track.id.in_(body.library_track_ids)))).all()
        by_id = dict(rows)
        ids += [by_id[i] for i in body.library_track_ids if by_id.get(i)]
    if not ids:
        raise HTTPException(400, "Эти треки ещё не появились в Navidrome")
    nd = user_navidrome(user)
    try:
        await nd.update_playlist(playlist_id, add=ids)
    finally:
        await nd.aclose()
    return {"ok": True, "added": len(ids)}


@router.get("/{playlist_id}", response_model=PlaylistPageOut)
async def get_playlist(playlist_id: str, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    p = await nd_call(user, lambda nd: nd.get_playlist(playlist_id), not_found="Плейлист не найден")
    if not p.get("id"):
        raise HTTPException(404, "Плейлист не найден")
    return PlaylistPageOut(**pl_out(p).model_dump(), tracks=await songs_out(s, p.get("entry") or []))


@router.post("/{playlist_id}/remove")
async def remove(playlist_id: str, body: PlaylistRemoveIn, user: User = Depends(current_user)):
    """Takes songs out by their positions (a song may be in a playlist twice)."""
    await nd_call(user, lambda nd: nd.update_playlist(playlist_id, remove_indexes=sorted(set(body.indexes))),
                  not_found="Плейлист не найден")
    return {"ok": True}


@router.delete("/{playlist_id}")
async def delete(playlist_id: str, user: User = Depends(current_user)):
    await nd_call(user, lambda nd: nd.delete_playlist(playlist_id), not_found="Плейлист не найден")
    return {"ok": True}
