"""Screenshot recognition eval (PLAN.md 9, step 3.7): precision / recall of recognized rows vs. expected rows.

Usage (from hub/):  uv run python ../eval/run_eval.py [--only NAME ...] [--threshold 0.85] [--no-save]

Reads eval/expected.json ({"<file name>": {"source_app": "...", "items": [{"artist": "...", "title": "..."}]}}),
runs `extract_from_images` on eval/screenshots/<file name> and prints per-file and total precision/recall and cost.
LLM settings come from the environment, falling back to the repository's .env.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
sys.path.insert(0, str(ROOT / "hub"))


def load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines); existing environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


load_dotenv(ROOT / ".env")

from rapidfuzz import fuzz  # noqa: E402

from app.ai.extract import ExtractedItem, extract_from_images, norm_key  # noqa: E402
from app.ai.llm import LLMClient, LLMUsage, configured  # noqa: E402
from app.config import get_settings  # noqa: E402


def _ratio(a: str, b: str) -> float:
    return fuzz.ratio(a, b) / 100 if a or b else 1.0


def row_score(pred: ExtractedItem, exp: dict) -> float:
    """Similarity 0..1 of a recognized row and an expected row (normalized; truncated titles compared by prefix)."""
    e_title, e_artist = norm_key(exp.get("title")), norm_key(exp.get("artist"))
    p_artist = norm_key(pred.artist_raw)
    titles = [norm_key(pred.title_raw)]
    if pred.guess_full:
        titles.append(norm_key(pred.guess_full))
    best_title = 0.0
    for t in titles:
        s = _ratio(t, e_title)
        if pred.truncated and len(t) >= 3 and e_title.startswith(t):
            s = max(s, 0.95)
        best_title = max(best_title, s)
    artist = _ratio(p_artist, e_artist)
    if pred.truncated and len(p_artist) >= 3 and e_artist.startswith(p_artist):
        artist = max(artist, 0.95)
    # word order / "feat." differences in the artist field
    artist = max(artist, fuzz.token_set_ratio(p_artist, e_artist) / 100 * 0.95)
    return 0.6 * best_title + 0.4 * artist


def match_rows(pred: list[ExtractedItem], exp: list[dict], threshold: float) -> list[tuple[int, int, float]]:
    """Greedy one-to-one matching by best score >= threshold."""
    pairs = sorted(
        ((row_score(p, e), i, j) for i, p in enumerate(pred) for j, e in enumerate(exp)),
        reverse=True,
    )
    used_p: set[int] = set()
    used_e: set[int] = set()
    out: list[tuple[int, int, float]] = []
    for s, i, j in pairs:
        if s < threshold:
            break
        if i in used_p or j in used_e:
            continue
        used_p.add(i)
        used_e.add(j)
        out.append((i, j, s))
    return out


def fmt_row(artist: str, title: str) -> str:
    return f"{artist} — {title}"


async def run(args: argparse.Namespace) -> int:
    expected_path = EVAL_DIR / "expected.json"
    shots_dir = EVAL_DIR / "screenshots"
    if not expected_path.is_file():
        print(f"Нет {expected_path}. Скопируйте expected.example.json в expected.json и заполните (см. README.md).")
        return 2
    expected: dict[str, dict] = json.loads(expected_path.read_text(encoding="utf-8"))
    settings = get_settings()
    if not configured(settings):
        print("LLM не настроен: задайте LLM_API_KEY (или локальный LLM_BASE_URL) в .env")
        return 2

    names = [n for n in expected if not args.only or n in args.only]
    total_pred = total_exp = total_ok = 0
    app_ok = app_total = 0
    usage = LLMUsage()
    report: dict[str, dict] = {}
    print(f"model={settings.llm_model} base_url={settings.llm_base_url} files={len(names)} threshold={args.threshold}")
    async with LLMClient(settings) as llm:
        for name in names:
            exp = expected[name]
            path = shots_dir / name
            if not path.is_file():
                print(f"\n[{name}] нет файла {path}, пропуск")
                continue
            t0 = time.monotonic()
            try:
                ex, u = await extract_from_images([path.read_bytes()], llm)
            except Exception as e:  # keep going: one broken file must not stop the eval
                print(f"\n[{name}] ОШИБКА: {e}")
                report[name] = {"error": str(e)}
                continue
            usage = usage + u
            exp_items = exp.get("items") or []
            matches = match_rows(ex.items, exp_items, args.threshold)
            n_ok = len(matches)
            prec = n_ok / len(ex.items) if ex.items else (1.0 if not exp_items else 0.0)
            rec = n_ok / len(exp_items) if exp_items else 1.0
            total_pred += len(ex.items)
            total_exp += len(exp_items)
            total_ok += n_ok
            app_flag = ""
            if exp.get("source_app"):
                app_total += 1
                app_ok += ex.source_app == exp["source_app"]
                app_flag = "" if ex.source_app == exp["source_app"] else f" (ожидалось {exp['source_app']})"
            print(
                f"\n[{name}] P={prec:.2f} R={rec:.2f} строк {len(ex.items)}/{len(exp_items)} "
                f"app={ex.source_app}{app_flag} page={ex.page_type} ${u.cost_usd:.5f} {time.monotonic() - t0:.1f}s"
            )
            matched_p = {i for i, _, _ in matches}
            matched_e = {j for _, j, _ in matches}
            for j, e in enumerate(exp_items):
                if j not in matched_e:
                    print(f"   - пропущено: {fmt_row(e.get('artist', ''), e.get('title', ''))}")
            for i, p in enumerate(ex.items):
                if i not in matched_p:
                    trunc = " [обрезано]" if p.truncated else ""
                    print(f"   + лишнее:    {fmt_row(p.artist_raw, p.title_raw)}{trunc}")
            report[name] = {
                "precision": prec, "recall": rec, "source_app": ex.source_app, "page_type": ex.page_type,
                "cost_usd": u.cost_usd, "extraction": ex.model_dump(),
            }

    precision = total_ok / total_pred if total_pred else 0.0
    recall = total_ok / total_exp if total_exp else 0.0
    print("\n" + "=" * 60)
    print(f"ИТОГО: precision={precision:.3f} recall={recall:.3f} (верных строк {total_ok}, "
          f"распознано {total_pred}, ожидалось {total_exp})")
    if app_total:
        print(f"source_app верно: {app_ok}/{app_total}")
    print(f"токены: in={usage.tokens_in} out={usage.tokens_out}, стоимость ${usage.cost_usd:.4f}")
    print("Цель FR-6: recall >= 0.90")
    if not args.no_save:
        out_dir = EVAL_DIR / "results"
        out_dir.mkdir(exist_ok=True)
        out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}.json"
        out.write_text(json.dumps({
            "model": settings.llm_model, "precision": precision, "recall": recall,
            "cost_usd": usage.cost_usd, "files": report,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"подробности: {out}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", help="only these file names from expected.json")
    ap.add_argument("--threshold", type=float, default=0.85, help="row match threshold 0..1 (default 0.85)")
    ap.add_argument("--no-save", action="store_true", help="do not write eval/results/<time>.json")
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
